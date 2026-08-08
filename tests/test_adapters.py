"""어댑터 — HTTP 리시버, WebSocket URL, ts 코덱, 호환 shim."""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx
import pytest

import fixtures
from conftest import FakeClient
from mattermost_bolt import App
from mattermost_bolt.adapter.http_receiver import HTTPReceiver, _parse_body, wsgi_app
from mattermost_bolt.adapter.socket_mode import SocketModeHandler
from mattermost_bolt.adapter.ws_client import to_websocket_url
from mattermost_bolt.ts import TsCodec, looks_like_post_id

# -- WebSocket URL ---------------------------------------------------------


@pytest.mark.parametrize(
    ("server", "expected"),
    [
        ("http://mm.test", "ws://mm.test/api/v4/websocket"),
        ("https://mm.test", "wss://mm.test/api/v4/websocket"),
        ("https://mattermost.example.com/", "ws://mattermost.example.com/api/v4/websocket"),
        ("mm.test", "ws://mm.test/api/v4/websocket"),
        ("wss://mm.test", "wss://mm.test/api/v4/websocket"),
    ],
)
def test_websocket_url(server: str, expected: str) -> None:
    assert to_websocket_url(server) == expected


# -- ts 코덱 (결정 D4) -----------------------------------------------------


def test_post_id_mode_is_a_passthrough() -> None:
    codec = TsCodec("post_id")
    assert codec.encode(fixtures.POST_ID, 1786160418395) == fixtures.POST_ID
    assert codec.decode(fixtures.POST_ID) == fixtures.POST_ID


def test_epoch_mode_round_trips() -> None:
    codec = TsCodec("epoch")
    ts = codec.encode(fixtures.POST_ID, 1786160418395)
    assert ts == "1786160418.395000"
    assert float(ts)
    assert codec.decode(ts) == fixtures.POST_ID
    assert codec.ts_for(fixtures.POST_ID) == ts


def test_epoch_mode_falls_back_when_unknown() -> None:
    codec = TsCodec("epoch")
    assert codec.decode("9999999999.000000") == "9999999999.000000"


def test_epoch_cache_is_bounded() -> None:
    codec = TsCodec("epoch", maxsize=3)
    for index in range(10):
        codec.encode(f"post{index:022d}", 1700000000000 + index)
    assert len(codec._ts_to_id) == 3
    assert len(codec._id_to_ts) == 3


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="ts_format"):
        TsCodec("nanoseconds")


def test_post_id_detection() -> None:
    assert looks_like_post_id(fixtures.POST_ID)
    assert not looks_like_post_id("#bolt-dev")
    assert not looks_like_post_id("1754620800.123456")


# -- HTTP 본문 파싱 --------------------------------------------------------


def test_command_body_is_form_encoded() -> None:
    parsed = _parse_body(
        "application/x-www-form-urlencoded",
        b"command=%2Fping&text=hello+world&channel_id=c1",
    )
    assert parsed == {"command": "/ping", "text": "hello world", "channel_id": "c1"}


def test_interaction_body_is_json() -> None:
    parsed = _parse_body("application/json", b'{"context":{"action_id":"a"}}')
    assert parsed["context"]["action_id"] == "a"


def test_malformed_json_does_not_explode() -> None:
    assert _parse_body("application/json", b"{broken") == {}
    assert _parse_body("application/json", b"") == {}


# -- HTTP 리시버 (실제 소켓) -----------------------------------------------


@pytest.fixture
def running_app(fake_client: FakeClient) -> Any:
    app = App(
        token="t" * 26,
        server_url="http://mm.test",
        client=fake_client,  # type: ignore[arg-type]
        mode="http",
        request_url="http://app.test",
    )
    app._bot_user_id = "b" * 26

    @app.command("/boltspike")
    def on_command(ack):
        ack("pong")

    @app.action("go")
    def on_action(ack, respond):
        ack()
        respond("clicked")

    receiver = HTTPReceiver(app, host="127.0.0.1", port=0)
    receiver.start()
    port = receiver._server.server_address[1]
    yield f"http://127.0.0.1:{port}", app
    receiver.stop()


def test_receiver_handles_command(running_app: Any) -> None:
    base, _ = running_app
    response = httpx.post(f"{base}/mmbolt/commands", data=fixtures.http_command_form(), timeout=10)
    assert response.status_code == 200
    assert response.json() == {"text": "pong", "response_type": "ephemeral"}


def test_receiver_handles_action(running_app: Any) -> None:
    base, _ = running_app
    body = fixtures.http_action_body(action_id="go")
    response = httpx.post(f"{base}/mmbolt/actions", json=body, timeout=10)
    assert response.status_code == 200
    assert response.json()["ephemeral_text"] == "clicked"


def test_receiver_health(running_app: Any) -> None:
    base, _ = running_app
    assert httpx.get(f"{base}/mmbolt/health", timeout=10).json() == {"ok": True}


def test_receiver_unknown_path_is_404(running_app: Any) -> None:
    base, _ = running_app
    assert httpx.post(f"{base}/nope", json={}, timeout=10).status_code == 404


# -- WSGI ------------------------------------------------------------------


def test_wsgi_app_routes_commands(http_app: App) -> None:
    @http_app.command("/boltspike")
    def on_command(ack):
        ack("pong")

    application = wsgi_app(http_app)
    captured: list[Any] = []

    def start_response(status: str, headers: Any) -> None:
        captured.append(status)

    from io import BytesIO
    from urllib.parse import urlencode

    payload = urlencode(fixtures.http_command_form()).encode()
    environ: dict[str, Any] = {
        "PATH_INFO": "/mmbolt/commands",
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(payload)),
        "wsgi.input": BytesIO(payload),
    }
    body = b"".join(application(environ, start_response))
    assert captured == ["200 OK"]
    assert json.loads(body)["text"] == "pong"


# -- SocketModeHandler -----------------------------------------------------


def test_socket_mode_handler_accepts_slack_signature(app: App) -> None:
    """``SocketModeHandler(app, app_token)`` 형태를 그대로 받는다."""
    handler = SocketModeHandler(app, "xapp-1-unused")
    assert handler.app is app
    assert handler.app_token == "xapp-1-unused"


# -- compat shim (결정 D6) -------------------------------------------------


def test_shim_replaces_slack_bolt_imports() -> None:
    from mattermost_bolt.compat import install, uninstall

    try:
        install()
        import slack_bolt
        from slack_bolt import App as ShimApp
        from slack_bolt.adapter.socket_mode import SocketModeHandler as ShimHandler
        from slack_sdk.errors import SlackApiError

        from mattermost_bolt import App as RealApp
        from mattermost_bolt.errors import MattermostApiError

        assert ShimApp is RealApp
        assert ShimHandler is SocketModeHandler
        assert SlackApiError is MattermostApiError
        assert slack_bolt.__mmbolt_shim__ is True
    finally:
        uninstall()
        for name in list(sys.modules):
            if name.startswith(("slack_bolt", "slack_sdk")):
                del sys.modules[name]
