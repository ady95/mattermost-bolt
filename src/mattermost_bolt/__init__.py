"""mattermost-bolt — Slack Bolt 호환 인터페이스를 유지하는 Mattermost 앱 프레임워크.

Slack Bolt 앱을 옮길 때 바뀌는 것은 초기화 세 줄뿐이다::

    from mattermost_bolt import App
    from mattermost_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=MM_BOT_TOKEN, server_url="http://mattermost.example.com")

    @app.message("hello")
    def handle(message, say):
        say(f"Hey there <@{message['user']}>!")

    SocketModeHandler(app).start()
"""

from __future__ import annotations

from .app import App, Listener
from .context import BoltContext
from .errors import (
    BoltError,
    MattermostApiError,
    SlackApiError,
    UnsupportedFeatureError,
)
from .listener.args import Ack, Respond, Say
from .request import BoltRequest, BoltResponse
from .ts import TsCodec
from .web.client import WebClient
from .web.response import MattermostResponse, SlackResponse

__version__ = "0.1.0"

__all__ = [
    "Ack",
    "App",
    "BoltContext",
    "BoltError",
    "BoltRequest",
    "BoltResponse",
    "Listener",
    "MattermostApiError",
    "MattermostResponse",
    "Respond",
    "Say",
    "SlackApiError",
    "SlackResponse",
    "TsCodec",
    "UnsupportedFeatureError",
    "WebClient",
    "__version__",
]
