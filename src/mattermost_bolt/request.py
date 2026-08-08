"""``BoltRequest`` / ``BoltResponse``.

WebSocket 이벤트와 HTTP 인터랙션이라는 서로 다른 두 수신 경로를
하나의 요청 객체로 합류시킨다 (실행계획서 결정 D2).
"""

from __future__ import annotations

import json
from typing import Any

from .context import BoltContext

# 요청 종류 — 리스너 레지스트리의 1차 분기 키.
KIND_EVENT = "event"
KIND_COMMAND = "command"
KIND_ACTION = "action"
KIND_VIEW = "view"
KIND_OPTIONS = "options"

# 수신 경로.
SOURCE_WS = "ws"
SOURCE_HTTP = "http"


class BoltRequest:
    """정규화된 수신 요청."""

    __slots__ = ("body", "context", "headers", "kind", "raw", "source")

    def __init__(
        self,
        *,
        kind: str,
        body: dict[str, Any],
        source: str,
        raw: Any = None,
        context: BoltContext | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.kind = kind
        self.body = body
        self.source = source
        self.raw = raw
        self.context = context if context is not None else BoltContext()
        self.headers = headers or {}

    @property
    def payload(self) -> dict[str, Any]:
        """Slack Bolt 의 ``payload`` 인자에 해당하는 부분 페이로드."""
        if self.kind == KIND_EVENT:
            return self.body.get("event", {})
        if self.kind == KIND_ACTION:
            actions = self.body.get("actions") or [{}]
            return actions[0]
        if self.kind == KIND_VIEW:
            return self.body.get("view", {})
        return self.body

    def __repr__(self) -> str:  # pragma: no cover - 디버깅용
        return f"<BoltRequest kind={self.kind} source={self.source}>"


class BoltResponse:
    """HTTP 경로에서 Mattermost 로 돌려줄 응답.

    WebSocket 경로에서는 사용되지 않는다(``ack`` 가 no-op).
    """

    __slots__ = ("body", "headers", "status")

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}

    def to_bytes(self) -> bytes:
        if isinstance(self.body, (dict, list)):
            self.headers.setdefault("Content-Type", "application/json; charset=utf-8")
            return json.dumps(self.body, ensure_ascii=False).encode("utf-8")
        self.headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        return str(self.body or "").encode("utf-8")

    def __repr__(self) -> str:  # pragma: no cover - 디버깅용
        return f"<BoltResponse status={self.status}>"
