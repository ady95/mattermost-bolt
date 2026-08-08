"""``SocketModeHandler`` 호환 진입점.

Slack 앱의 마지막 두 줄은 보통 이렇다::

    from slack_bolt.adapter.socket_mode import SocketModeHandler
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()

Mattermost 에는 Socket Mode 가 없지만, 이벤트 수신은 WebSocket 으로 이뤄지므로
같은 이름·같은 호출 형태를 유지한다. ``app_token`` 은 받되 사용하지 않는다
(Mattermost 는 봇 토큰 하나로 WebSocket 인증까지 처리한다).
"""

from __future__ import annotations

from typing import Any


class SocketModeHandler:
    """Slack ``SocketModeHandler`` 와 같은 표면을 갖는 실행기."""

    def __init__(
        self,
        app: Any,
        app_token: str | None = None,
        *,
        port: int | None = None,
        **_ignored: Any,
    ) -> None:
        self.app = app
        # Mattermost 는 앱 토큰 개념이 없다. 인자를 받아 호환성만 유지한다.
        self.app_token = app_token
        self.port = port

    def start(self) -> None:
        """블로킹 실행. 인터랙티브 리스너가 있으면 HTTP 리시버도 함께 뜬다."""
        kwargs = {"port": self.port} if self.port else {}
        self.app.start(**kwargs)

    def connect(self) -> None:
        """논블로킹 실행."""
        kwargs = {"port": self.port} if self.port else {}
        self.app.start(blocking=False, **kwargs)

    def close(self) -> None:
        self.app.stop()

    # Slack SDK 호환 별칭.
    def start_async(self) -> None:  # pragma: no cover - 별칭
        self.connect()

    def disconnect(self) -> None:  # pragma: no cover - 별칭
        self.close()
