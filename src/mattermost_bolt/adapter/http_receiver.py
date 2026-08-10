"""HTTP 리시버 — Mattermost 가 앱으로 보내는 인바운드 요청을 받는다.

slash command, 인터랙티브 액션, 다이얼로그 제출은 Mattermost 서버가
등록된 URL 로 POST 한다. WebSocket 으로는 오지 않는다(결정 D2).

의존성을 늘리지 않기 위해 표준 라이브러리 ``ThreadingHTTPServer`` 를 쓴다.
운영에서 리버스 프록시 뒤에 두거나 WSGI 서버를 쓰고 싶다면
``wsgi_app(app)`` 으로 WSGI 애플리케이션을 얻을 수 있다.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs

from ..request import BoltResponse

_logger = logging.getLogger("mattermost_bolt.http")

JSON_CONTENT = "application/json"


def parse_body(content_type: str, raw: bytes) -> dict[str, Any]:
    """Mattermost 는 command 를 폼으로, 인터랙션을 JSON 으로 보낸다.

    FastAPI 등 외부 프레임워크에 얹을 때도 그대로 쓸 수 있도록 공개한다.
    ``route()`` 와 짝을 이룬다.
    """
    text = raw.decode("utf-8") if raw else ""
    if JSON_CONTENT in (content_type or ""):
        try:
            parsed = json.loads(text) if text else {}
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {"payload": parsed}
    return {key: values[0] for key, values in parse_qs(text).items()}


def route(app: Any, path: str, body: dict[str, Any]) -> BoltResponse | None:
    """경로에 맞는 App 진입점으로 넘긴다. 모르는 경로는 ``None``."""
    prefix = app.path_prefix
    if path == f"{prefix}/commands":
        return app.handle_command(body)
    if path == f"{prefix}/actions":
        return app.handle_action(body)
    if path in (f"{prefix}/dialogs", f"{prefix}/views"):
        return app.handle_dialog(body)
    return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "mattermost-bolt"
    app: Any = None
    logger: logging.Logger = _logger

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = parse_body(self.headers.get("Content-Type", ""), raw)
        path = self.path.split("?", 1)[0]

        try:
            response = route(self.app, path, body)
        except Exception:
            self.logger.exception("HTTP 요청 처리 실패: %s", path)
            self._send(
                BoltResponse(
                    status=200,
                    body={
                        "response_type": "ephemeral",
                        "text": "요청 처리 중 오류가 발생했습니다.",
                    },
                )
            )
            return

        if response is None:
            self._send(BoltResponse(status=404, body="not found"))
            return
        self._send(response)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == f"{self.app.path_prefix}/health":
            self._send(BoltResponse(body={"ok": True}))
            return
        self._send(BoltResponse(status=404, body="not found"))

    def _send(self, response: BoltResponse) -> None:
        payload = response.to_bytes()
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        self.logger.debug("%s - %s", self.address_string(), format % args)


class HTTPReceiver:
    """백그라운드 스레드에서 도는 HTTP 서버."""

    def __init__(self, app: Any, *, host: str = "0.0.0.0", port: int = 8099) -> None:
        self.app = app
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = type(
            "BoundHandler",
            (_Handler,),
            {"app": self.app, "logger": getattr(self.app, "logger", _logger)},
        )
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mmbolt-http", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def wsgi_app(app: Any) -> Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]:
    """Flask/gunicorn 등에 얹을 수 있는 WSGI 애플리케이션을 만든다.

    from mattermost_bolt.adapter.http_receiver import wsgi_app
    application = wsgi_app(bolt_app)
    """

    def application(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")

        if method == "GET" and path == f"{app.path_prefix}/health":
            return _wsgi_send(start_response, BoltResponse(body={"ok": True}))
        if method != "POST":
            return _wsgi_send(start_response, BoltResponse(status=405, body="method not allowed"))

        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        raw = environ["wsgi.input"].read(length) if length else b""
        body = parse_body(environ.get("CONTENT_TYPE", ""), raw)

        response = route(app, path, body)
        if response is None:
            return _wsgi_send(start_response, BoltResponse(status=404, body="not found"))
        return _wsgi_send(start_response, response)

    return application


_STATUS_TEXT = {200: "200 OK", 404: "404 Not Found", 405: "405 Method Not Allowed"}


def _wsgi_send(start_response: Callable[..., Any], response: BoltResponse) -> Iterable[bytes]:
    payload = response.to_bytes()
    headers: tuple[tuple[str, str], ...] = (
        *tuple(response.headers.items()),
        ("Content-Length", str(len(payload))),
    )
    start_response(_STATUS_TEXT.get(response.status, f"{response.status} OK"), list(headers))
    return [payload]
