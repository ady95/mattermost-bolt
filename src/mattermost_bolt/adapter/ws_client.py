"""Mattermost WebSocket 클라이언트.

Slack Socket Mode 자리에 놓이는 이벤트 수신 경로다. 연결이 끊겨도 앱이
멈추면 안 되므로 지수 백오프 재연결을 내장한다(실행계획서 G6).

프로토콜 요약:

1. ``ws(s)://<host>/api/v4/websocket`` 로 연결
2. ``authentication_challenge`` 액션으로 봇 토큰 전송
3. 서버가 ``hello`` 이벤트를 보내면 인증 성공
4. 이후 ``{"event": ..., "data": {...}, "broadcast": {...}, "seq": N}`` 수신
"""

from __future__ import annotations

import json
import logging
import random
import threading
from typing import Any, Callable

from websockets.sync.client import connect

_logger = logging.getLogger("mattermost_bolt.ws")

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0
DEFAULT_PING_INTERVAL = 30.0


def to_websocket_url(server_url: str) -> str:
    """``http(s)://host`` → ``ws(s)://host/api/v4/websocket``."""
    base = server_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    elif not base.startswith(("ws://", "wss://")):
        base = "ws://" + base
    return f"{base}/api/v4/websocket"


class MattermostWebSocketClient:
    """이벤트 수신 루프."""

    def __init__(
        self,
        *,
        token: str,
        server_url: str,
        on_event: Callable[[dict[str, Any]], None],
        logger: logging.Logger | None = None,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        max_backoff: float = MAX_BACKOFF,
    ) -> None:
        self.token = token
        self.url = to_websocket_url(server_url)
        self.on_event = on_event
        self.logger = logger or _logger
        self.ping_interval = ping_interval
        self.max_backoff = max_backoff

        self._closed = threading.Event()
        self._connection: Any = None
        self._seq = 1
        self.connected = threading.Event()

    # -- 공개 API -----------------------------------------------------------

    def run_forever(self) -> None:
        """끊기면 다시 붙는다. 호출자를 블로킹한다."""
        backoff = INITIAL_BACKOFF
        while not self._closed.is_set():
            try:
                self._session()
                backoff = INITIAL_BACKOFF
            except Exception as error:
                if self._closed.is_set():
                    break
                jitter = random.uniform(0, backoff * 0.25)
                wait = min(backoff + jitter, self.max_backoff)
                self.logger.warning(
                    "WebSocket 연결이 끊겼습니다 (%s). %.1f초 후 재연결합니다.",
                    error,
                    wait,
                )
                if self._closed.wait(wait):
                    break
                backoff = min(backoff * 2, self.max_backoff)

    def close(self) -> None:
        self._closed.set()
        self.connected.clear()
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def send(self, action: str, data: dict[str, Any] | None = None) -> None:
        """서버로 액션 프레임을 보낸다."""
        connection = self._connection
        if connection is None:
            raise RuntimeError("WebSocket 이 연결되어 있지 않습니다")
        self._seq += 1
        connection.send(json.dumps({"seq": self._seq, "action": action, "data": data or {}}))

    # -- 내부 --------------------------------------------------------------

    def _session(self) -> None:
        self.logger.debug("WebSocket 연결 시도: %s", self.url)
        with connect(
            self.url,
            open_timeout=15,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_interval,
            max_size=None,
        ) as connection:
            self._connection = connection
            self._seq = 1
            connection.send(
                json.dumps(
                    {
                        "seq": self._seq,
                        "action": "authentication_challenge",
                        "data": {"token": self.token},
                    }
                )
            )
            try:
                self._read_loop(connection)
            finally:
                self._connection = None
                self.connected.clear()

    def _read_loop(self, connection: Any) -> None:
        for raw_message in connection:
            if self._closed.is_set():
                return
            try:
                frame = json.loads(raw_message)
            except (TypeError, ValueError):
                self.logger.debug("JSON 이 아닌 프레임을 무시합니다: %r", raw_message[:120])
                continue

            if frame.get("event") == "hello":
                self.connected.set()
                self.logger.info(
                    "WebSocket 인증 완료 (server_version=%s)",
                    (frame.get("data") or {}).get("server_version", "?"),
                )
                continue

            # 액션 응답(seq_reply)은 이벤트가 아니다. 인증 실패만 확인한다.
            if "seq_reply" in frame:
                error = frame.get("error")
                if error:
                    raise RuntimeError(f"WebSocket 인증 실패: {error}")
                continue

            if not frame.get("event"):
                continue

            try:
                self.on_event(frame)
            except Exception:
                self.logger.exception("이벤트 처리 중 예외 (event=%s)", frame.get("event"))
