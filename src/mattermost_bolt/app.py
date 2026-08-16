"""``App`` — Slack Bolt 호환 진입점.

두 수신 경로(WebSocket 이벤트 / HTTP 인터랙션)를 하나의 리스너 레지스트리로
합류시킨다 (실행계획서 결정 D2). 핸들러 코드는 어느 경로로 들어온 요청인지
알 필요가 없다.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from re import Pattern
from typing import Any, Callable, Union

from .context import BoltContext
from .errors import BoltError
from .listener.args import Ack, build_kwargs
from .listener.matcher import (
    Matcher,
    action_matcher,
    command_matcher,
    event_matcher,
    message_matcher,
    view_matcher,
)
from .payload.action import normalize_action, normalize_dialog
from .payload.command import build_command_from_message, normalize_command
from .payload.event import normalize_ws_event
from .request import (
    KIND_ACTION,
    KIND_COMMAND,
    KIND_EVENT,
    KIND_VIEW,
    SOURCE_HTTP,
    SOURCE_WS,
    BoltRequest,
    BoltResponse,
)
from .ts import TsCodec
from .web.client import WebClient

DEFAULT_PATH_PREFIX = "/mmbolt"
DEFAULT_PORT = 8099

MODE_SOCKET = "socket"
MODE_HTTP = "http"
MODE_AUTO = "auto"

PatternLike = Union[str, Pattern[str]]


class Listener:
    """등록된 리스너 하나."""

    __slots__ = ("auto_ack", "callback", "kind", "matchers", "middleware")

    def __init__(
        self,
        *,
        kind: str,
        matchers: Sequence[Matcher],
        middleware: Sequence[Callable[..., Any]],
        callback: Callable[..., Any],
        auto_ack: bool = True,
    ) -> None:
        self.kind = kind
        self.matchers = list(matchers)
        self.middleware = list(middleware)
        self.callback = callback
        self.auto_ack = auto_ack

    def matches(self, request: BoltRequest) -> bool:
        return all(m(request) for m in self.matchers)


class App:
    """Mattermost 앱.

    Slack Bolt 대비 추가되는 필수 인자는 ``server_url`` 하나뿐이다::

        app = App(token=MM_BOT_TOKEN, server_url="http://mattermost.example.com")
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        server_url: str | None = None,
        team: str | None = None,
        mode: str = MODE_AUTO,
        request_url: str | None = None,
        path_prefix: str = DEFAULT_PATH_PREFIX,
        ts_format: str = "post_id",
        ignore_self: bool = True,
        convert_mrkdwn: bool = True,
        verification_token: str | None = None,
        raise_error_for_unhandled_request: bool = False,
        logger: logging.Logger | None = None,
        client: WebClient | None = None,
        max_workers: int = 8,
        verify_ssl: bool = True,
    ) -> None:
        if client is None and not (token and server_url):
            raise BoltError(
                "App(token=..., server_url=...) 이 필요합니다. "
                "server_url 은 Mattermost 주소입니다 (예: https://mattermost.example.com)."
            )

        self.logger = logger or logging.getLogger("mattermost_bolt")
        self.token = token
        self.server_url = (server_url or "").rstrip("/")
        self.team = team
        self.mode = mode
        self.path_prefix = "/" + path_prefix.strip("/")
        self.request_url = (request_url or "").rstrip("/")
        self.ignore_self = ignore_self
        self.verification_token = verification_token
        self.raise_error_for_unhandled_request = raise_error_for_unhandled_request

        self.ts_codec = TsCodec(ts_format)
        self.client: WebClient = client or WebClient(
            token=token or "",
            server_url=self.server_url,
            team=team,
            ts_codec=self.ts_codec,
            action_url=self.action_url,
            dialog_url=self.dialog_url,
            convert_mrkdwn=convert_mrkdwn,
            logger=self.logger,
            verify=verify_ssl,
        )

        self._listeners: list[Listener] = []
        self._middleware: list[Callable[..., Any]] = []
        self._error_handler: Callable[..., Any] | None = None
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mmbolt")
        self._bot_user_id: str | None = None
        self._ws: Any = None
        self._http_server: Any = None
        self._closing = threading.Event()

    # -- URL ---------------------------------------------------------------

    @property
    def action_url(self) -> str:
        """Mattermost 가 인터랙션을 보낼 URL. ``request_url`` 이 없으면 빈 문자열."""
        if not self.request_url:
            return ""
        return f"{self.request_url}{self.path_prefix}/actions"

    @property
    def dialog_url(self) -> str:
        if not self.request_url:
            return ""
        return f"{self.request_url}{self.path_prefix}/dialogs"

    @property
    def command_url(self) -> str:
        if not self.request_url:
            return ""
        return f"{self.request_url}{self.path_prefix}/commands"

    # -- 등록 API -----------------------------------------------------------

    def use(self, middleware: Callable[..., Any]) -> Callable[..., Any]:
        """전역 미들웨어 등록 (``@app.use``)."""
        self._middleware.append(middleware)
        return middleware

    middleware = use

    def error(self, callback: Callable[..., Any]) -> Callable[..., Any]:
        """전역 에러 핸들러 등록 (``@app.error``)."""
        self._error_handler = callback
        return callback

    def event(
        self,
        constraint: str | dict[str, Any],
        middleware: Sequence[Callable[..., Any]] | None = None,
        **matchers: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register(KIND_EVENT, [event_matcher(constraint)], middleware)

    def message(
        self,
        pattern: PatternLike | None = None,
        middleware: Sequence[Callable[..., Any]] | None = None,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """``@app.message(...)``.

        기본적으로 봇 메시지(``subtype=bot_message``)와 시스템 메시지는 건너뛴다.
        Slack Bolt 와 동일한 기본 동작이며, 무한 루프를 막는 안전장치다.
        """
        ignore = kwargs.pop(
            "ignore_subtypes",
            ("bot_message", "message_deleted", "channel_join", "channel_leave"),
        )
        return self._register(
            KIND_EVENT, [message_matcher(pattern, ignore_subtypes=ignore)], middleware
        )

    def command(
        self,
        pattern: PatternLike,
        middleware: Sequence[Callable[..., Any]] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register(KIND_COMMAND, [command_matcher(pattern)], middleware)

    def action(
        self,
        constraint: str | Pattern[str] | dict[str, Any],
        middleware: Sequence[Callable[..., Any]] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register(KIND_ACTION, [action_matcher(constraint)], middleware)

    def view(
        self,
        constraint: str | Pattern[str] | dict[str, Any],
        middleware: Sequence[Callable[..., Any]] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register(KIND_VIEW, [view_matcher(constraint)], middleware)

    def view_closed(
        self,
        constraint: str | Pattern[str] | dict[str, Any],
        middleware: Sequence[Callable[..., Any]] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register(KIND_VIEW, [view_matcher(constraint, closed=True)], middleware)

    def shortcut(
        self,
        constraint: str | Pattern[str] | dict[str, Any],
        middleware: Sequence[Callable[..., Any]] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Mattermost 에 Global/Message Shortcut 개념이 없다.

        전용 slash command 로 폴백한다. ``@app.shortcut("run_report")`` 는
        ``/run_report`` 명령으로 동작한다.
        """
        name = constraint if isinstance(constraint, str) else str(constraint)
        self.logger.warning(
            "Mattermost 에는 shortcut 이 없습니다. %r 을 슬래시 명령 '/%s' 로 등록합니다.",
            name,
            name,
        )
        return self._register(KIND_COMMAND, [command_matcher(name)], middleware)

    def _register(
        self,
        kind: str,
        matchers: Sequence[Matcher],
        middleware: Sequence[Callable[..., Any]] | None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(callback: Callable[..., Any]) -> Callable[..., Any]:
            self._listeners.append(
                Listener(
                    kind=kind,
                    matchers=matchers,
                    middleware=list(middleware or []),
                    callback=callback,
                )
            )
            return callback

        return decorator

    # -- 수신 진입점 --------------------------------------------------------

    def handle_ws_frame(self, raw: dict[str, Any]) -> None:
        """Mattermost WebSocket 프레임 하나를 처리한다 (비동기 디스패치)."""
        body = normalize_ws_event(raw, self.ts_codec)
        if body is None:
            return
        event = body.get("event", {})

        if self.ignore_self and self._is_self(event):
            return

        request = BoltRequest(kind=KIND_EVENT, body=body, source=SOURCE_WS, raw=raw)
        self._seed_context(
            request,
            channel_id=event.get("channel"),
            user_id=event.get("user"),
            team_id=body.get("team_id"),
        )
        self._submit(request)

        # Pseudo Socket Mode (결정 D3): WS 로 받은 텍스트에서 명령을 합성한다.
        if self.mode == MODE_SOCKET and event.get("type") == "message":
            command_body = build_command_from_message(event.get("text", ""), event=event)
            if command_body and self._has_command_listener(command_body["command"]):
                cmd_request = BoltRequest(
                    kind=KIND_COMMAND, body=command_body, source=SOURCE_WS, raw=raw
                )
                self._seed_context(
                    cmd_request,
                    channel_id=command_body.get("channel_id"),
                    user_id=command_body.get("user_id"),
                    team_id=command_body.get("team_id"),
                )
                self._submit(cmd_request)

    def handle_command(self, form: dict[str, Any]) -> BoltResponse:
        """slash command HTTP 요청."""
        body = normalize_command(form)
        self._verify_token(body.get("token"))
        request = BoltRequest(kind=KIND_COMMAND, body=body, source=SOURCE_HTTP, raw=form)
        self._seed_context(
            request,
            channel_id=body.get("channel_id"),
            user_id=body.get("user_id"),
            team_id=body.get("team_id"),
            response_url=body.get("response_url"),
            trigger_id=body.get("trigger_id"),
        )
        return self._dispatch_sync(request)

    def handle_action(self, raw: dict[str, Any]) -> BoltResponse:
        """인터랙티브 액션 HTTP 요청."""
        body = normalize_action(raw, self.ts_codec)
        request = BoltRequest(kind=KIND_ACTION, body=body, source=SOURCE_HTTP, raw=raw)
        self._seed_context(
            request,
            channel_id=body["channel"]["id"],
            user_id=body["user"]["id"],
            team_id=body["team"]["id"],
            trigger_id=body.get("trigger_id"),
        )
        return self._dispatch_sync(request)

    def handle_dialog(self, raw: dict[str, Any]) -> BoltResponse:
        """다이얼로그 제출/취소 HTTP 요청."""
        body = normalize_dialog(raw)
        request = BoltRequest(kind=KIND_VIEW, body=body, source=SOURCE_HTTP, raw=raw)
        from .payload.action import _parse_state

        self._seed_context(
            request,
            channel_id=raw.get("channel_id"),
            user_id=raw.get("user_id"),
            team_id=raw.get("team_id"),
        )
        request.context["view_state_meta"] = _parse_state(raw.get("state"))
        return self._dispatch_sync(request)

    # -- 디스패치 -----------------------------------------------------------

    def _seed_context(self, request: BoltRequest, **values: Any) -> None:
        context: BoltContext = request.context
        context["client"] = self.client
        context["logger"] = self.logger
        context["token"] = self.token
        context["bot_user_id"] = self._bot_user_id
        for key, value in values.items():
            if value:
                context[key] = value

    def _submit(self, request: BoltRequest) -> None:
        self._executor.submit(self._dispatch_safely, request, None)

    def _dispatch_sync(self, request: BoltRequest) -> BoltResponse:
        response = BoltResponse()
        self._dispatch_safely(request, response)
        return response

    def _dispatch_safely(self, request: BoltRequest, response: BoltResponse | None) -> None:
        try:
            self._dispatch(request, response)
        except Exception as error:
            self._handle_error(error, request, response)

    def _dispatch(self, request: BoltRequest, response: BoltResponse | None) -> None:
        ack = Ack(request, response)
        matched = [listener for listener in self._listeners if listener.matches(request)]

        def run_listeners() -> None:
            if not matched:
                self._on_unhandled(request, response)
                return
            for listener in matched:
                self._run_listener(listener, request, response, ack)

        self._run_middleware(self._middleware, request, response, ack, run_listeners)

    def _run_middleware(
        self,
        middleware: Sequence[Callable[..., Any]],
        request: BoltRequest,
        response: BoltResponse | None,
        ack: Ack,
        terminal: Callable[[], None],
    ) -> None:
        """미들웨어 체인을 순서대로 실행한다. ``next()`` 를 부르지 않으면 중단된다."""
        index = 0

        def call_next() -> None:
            nonlocal index
            if index >= len(middleware):
                terminal()
                return
            current = middleware[index]
            index += 1
            kwargs = build_kwargs(
                current,
                request=request,
                response=response,
                client=self.client,
                logger=self.logger,
                ack=ack,
                next_callable=call_next,
            )
            current(**kwargs)

        call_next()

    def _run_listener(
        self,
        listener: Listener,
        request: BoltRequest,
        response: BoltResponse | None,
        ack: Ack,
    ) -> None:
        def invoke() -> None:
            kwargs = build_kwargs(
                listener.callback,
                request=request,
                response=response,
                client=self.client,
                logger=self.logger,
                ack=ack,
                next_callable=lambda: None,
            )
            listener.callback(**kwargs)
            if response is not None and not ack.called and listener.auto_ack:
                # ack() 를 잊은 핸들러도 Mattermost 에는 200 을 돌려줘야 한다.
                self.logger.debug(
                    "%r 가 ack() 를 호출하지 않아 빈 200 으로 응답합니다.",
                    getattr(listener.callback, "__name__", "listener"),
                )

        if listener.middleware:
            self._run_middleware(listener.middleware, request, response, ack, invoke)
        else:
            invoke()

    def _on_unhandled(self, request: BoltRequest, response: BoltResponse | None) -> None:
        if request.kind == KIND_EVENT:
            return  # 이벤트는 대부분 미처리가 정상이다.
        label = request.body.get("command") or request.payload.get("action_id") or "?"
        message = f"처리할 리스너가 없습니다: kind={request.kind} target={label}"
        if self.raise_error_for_unhandled_request:
            raise BoltError(message)
        self.logger.warning(message)
        if response is not None and not response.body:
            response.body = {"response_type": "ephemeral", "text": message}

    def _handle_error(
        self,
        error: Exception,
        request: BoltRequest,
        response: BoltResponse | None,
    ) -> None:
        if self._error_handler is not None:
            try:
                kwargs = build_kwargs(
                    self._error_handler,
                    request=request,
                    response=response,
                    client=self.client,
                    logger=self.logger,
                    ack=Ack(request, response),
                    next_callable=lambda: None,
                    extra={"error": error},
                )
                self._error_handler(**kwargs)
                return
            except Exception:
                self.logger.exception("에러 핸들러가 실패했습니다")
        self.logger.exception("리스너 처리 중 예외: %s", error)
        if response is not None and not response.body:
            response.status = 200
            response.body = {
                "response_type": "ephemeral",
                "text": "요청 처리 중 오류가 발생했습니다.",
            }

    # -- 보조 --------------------------------------------------------------

    def _is_self(self, event: dict[str, Any]) -> bool:
        bot_id = self._bot_user_id
        return bool(bot_id) and event.get("user") == bot_id

    def _has_command_listener(self, command: str) -> bool:
        probe = BoltRequest(kind=KIND_COMMAND, body={"command": command}, source=SOURCE_WS)
        return any(
            listener.kind == KIND_COMMAND and listener.matches(probe)
            for listener in self._listeners
        )

    def _verify_token(self, token: str | None) -> None:
        if self.verification_token and token != self.verification_token:
            raise BoltError("slash command 토큰이 일치하지 않습니다")

    def _requires_http(self) -> bool:
        return any(listener.kind in (KIND_ACTION, KIND_VIEW) for listener in self._listeners)

    def resolved_mode(self) -> str:
        if self.mode != MODE_AUTO:
            return self.mode
        return MODE_HTTP if (self._requires_http() or self.request_url) else MODE_SOCKET

    # -- 기동 --------------------------------------------------------------

    def start(
        self,
        port: int = DEFAULT_PORT,
        *,
        host: str = "0.0.0.0",
        blocking: bool = True,
        http_receiver: bool = True,
    ) -> None:
        """WebSocket 리스너와(필요 시) HTTP 리시버를 기동한다.

        Args:
            http_receiver: 내장 HTTP 리시버를 띄울지 여부. FastAPI/Flask 등
                외부 웹 프레임워크에 얹을 때는 ``False`` 로 두고
                ``handle_command`` / ``handle_action`` / ``handle_dialog`` 를
                직접 호출한다. 모드 판정과 콜백 URL 생성은 그대로 유지된다.
        """
        from .adapter.http_receiver import HTTPReceiver
        from .adapter.ws_client import MattermostWebSocketClient

        mode = self.resolved_mode()
        self._warn_on_mode(mode)

        auth = self.client.auth_test()
        self._bot_user_id = auth.get("user_id")
        self.logger.info(
            "Mattermost Bolt 기동: bot=@%s mode=%s server=%s",
            auth.get("user"),
            mode,
            self.server_url,
        )

        if mode == MODE_HTTP:
            if http_receiver:
                self._http_server = HTTPReceiver(self, host=host, port=port)
                self._http_server.start()
                self.logger.info(
                    "HTTP 리시버: http://%s:%d%s/{commands,actions,dialogs}",
                    host,
                    port,
                    self.path_prefix,
                )
            else:
                self.logger.info(
                    "내장 HTTP 리시버를 끕니다. 외부 프레임워크가 %s/{commands,actions,dialogs} "
                    "를 받아 App.handle_* 로 넘겨야 합니다.",
                    self.path_prefix,
                )
            if not self.request_url:
                self.logger.warning(
                    "request_url 이 없어 버튼·다이얼로그의 콜백 URL 을 만들 수 없습니다. "
                    "App(request_url='http://<이 호스트>:%d') 를 지정하세요.",
                    port,
                )

        self._ws = MattermostWebSocketClient(
            token=self.token or "",
            server_url=self.server_url,
            on_event=self.handle_ws_frame,
            logger=self.logger,
        )
        if blocking:
            try:
                self._ws.run_forever()
            except KeyboardInterrupt:  # pragma: no cover - 대화형 종료
                self.logger.info("종료 신호를 받았습니다.")
            finally:
                self.stop()
        else:
            threading.Thread(target=self._ws.run_forever, name="mmbolt-ws", daemon=True).start()

    def _warn_on_mode(self, mode: str) -> None:
        if mode == MODE_SOCKET and self._requires_http():
            kinds = {
                listener.kind
                for listener in self._listeners
                if listener.kind in (KIND_ACTION, KIND_VIEW)
            }
            self.logger.warning(
                "socket 모드에서는 %s 리스너가 절대 호출되지 않습니다. "
                "Mattermost 는 인터랙션을 HTTP 로만 전달합니다. "
                "App(mode='http', request_url=...) 로 기동하세요.",
                ", ".join(sorted(kinds)),
            )

    def stop(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        if self._ws is not None:
            self._ws.close()
        if self._http_server is not None:
            self._http_server.stop()
        self._executor.shutdown(wait=False)

    # Slack Bolt 호환 별칭 — HTTP 모드로 기동한다.
    def run(self, port: int = DEFAULT_PORT, **kwargs: Any) -> None:
        self.start(port, **kwargs)


def _normalize_pattern(pattern: PatternLike) -> Pattern[str]:  # pragma: no cover
    return pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
