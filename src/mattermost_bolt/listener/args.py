"""핸들러 인자 주입 — Slack Bolt 의 "필요한 인자만 선언" 방식을 재현한다.

    @app.message("hello")
    def handler(message, say, logger):   # 선언한 것만 주입된다
        ...

핸들러 시그니처를 ``inspect`` 로 읽어 필요한 이름만 채운다.
``**kwargs`` 를 선언한 핸들러에는 전부 넘긴다.
"""

from __future__ import annotations

import inspect
import logging
import weakref
from collections.abc import Sequence
from typing import Any, Callable

from ..request import (
    KIND_ACTION,
    KIND_COMMAND,
    KIND_EVENT,
    KIND_OPTIONS,
    KIND_VIEW,
    SOURCE_HTTP,
    BoltRequest,
    BoltResponse,
)


class Ack:
    """``ack()`` — Slack 의 3초 규칙에 대응하는 확인 응답.

    - HTTP 경로(slash command, 액션, 다이얼로그): 응답 본문을 확정한다.
    - WebSocket 경로: Mattermost 가 확인을 요구하지 않으므로 no-op 이며,
      호출 사실만 기록해 미호출 경고를 억제한다.
    """

    def __init__(self, request: BoltRequest, response: BoltResponse | None) -> None:
        self._request = request
        self._response = response
        self.called = False

    def __call__(
        self,
        text: Any = None,
        *,
        blocks: Sequence[dict[str, Any]] | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        response_type: str | None = None,
        response_action: str | None = None,
        errors: dict[str, str] | None = None,
        **_ignored: Any,
    ) -> None:
        self.called = True
        if self._response is None:
            return

        if response_action is not None:
            self._ack_view(response_action, errors)
            return

        if text is None and blocks is None and attachments is None:
            self._response.body = ""
            return

        body: dict[str, Any] = {}
        if isinstance(text, dict):
            body.update(text)
        elif text is not None:
            body["text"] = text
        if blocks is not None:
            body["blocks"] = list(blocks)
        if attachments is not None:
            body["attachments"] = list(attachments)
        body.setdefault("response_type", response_type or "ephemeral")
        self._response.body = body

    def _ack_view(self, response_action: str, errors: dict[str, str] | None) -> None:
        from ..payload.action import errors_to_dialog_response

        assert self._response is not None  # 호출부에서 None 을 걸러낸다
        if response_action == "errors" and errors:
            state_meta = self._request.context.get("view_state_meta") or {}
            self._response.body = errors_to_dialog_response(errors, state_meta)
            self._response.status = 200
            return
        # clear / update / push 는 Mattermost 다이얼로그에 대응물이 없다.
        # 제출을 정상 종료시키는 빈 200 응답으로 처리한다.
        self._response.body = {}


class Say:
    """``say()`` — 이벤트가 발생한 채널로 메시지를 보낸다."""

    def __init__(self, client: Any, channel: str | None, thread_ts: str | None = None):
        self._client = client
        self.channel = channel
        self.thread_ts = thread_ts

    def __call__(
        self,
        text: Any = None,
        *,
        channel: str | None = None,
        blocks: Sequence[dict[str, Any]] | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if isinstance(text, dict):
            kwargs = {**text, **kwargs}
            text = kwargs.pop("text", None)
            blocks = blocks or kwargs.pop("blocks", None)
            attachments = attachments or kwargs.pop("attachments", None)
        target = channel or self.channel
        if not target:
            raise ValueError("say() 를 쓸 채널을 알 수 없습니다. channel= 을 지정하세요.")
        return self._client.chat_postMessage(
            channel=target,
            text=text,
            blocks=blocks,
            attachments=attachments,
            thread_ts=thread_ts or self.thread_ts,
            **kwargs,
        )


class Respond:
    """``respond()`` — 원 요청자에게 회신한다.

    경로별로 수단이 다르다.

    - slash command(HTTP): ``response_url`` 로 지연 응답
    - 인터랙티브 액션: Mattermost 는 ``response_url`` 을 주지 않으므로
      HTTP 응답 본문의 ``update`` / ``ephemeral_text`` 를 사용한다
    - WebSocket 경로: ephemeral 메시지 API 로 대체
    """

    def __init__(
        self,
        *,
        client: Any,
        request: BoltRequest,
        response: BoltResponse | None,
    ) -> None:
        self._client = client
        self._request = request
        self._response = response

    def __call__(
        self,
        text: Any = None,
        *,
        blocks: Sequence[dict[str, Any]] | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        response_type: str = "ephemeral",
        replace_original: bool = False,
        delete_original: bool = False,
        **kwargs: Any,
    ) -> Any:
        if isinstance(text, dict):
            payload = dict(text)
            text = payload.pop("text", None)
            blocks = blocks or payload.pop("blocks", None)
            attachments = attachments or payload.pop("attachments", None)
            response_type = payload.pop("response_type", response_type)
            replace_original = payload.pop("replace_original", replace_original)
            delete_original = payload.pop("delete_original", delete_original)
            kwargs.update(payload)

        response_url = self._request.context.get("response_url")
        if response_url:
            body: dict[str, Any] = {"response_type": response_type}
            if text is not None:
                body["text"] = text
            if blocks:
                body["blocks"] = list(blocks)
            if attachments:
                body["attachments"] = list(attachments)
            if replace_original:
                body["replace_original"] = True
            if delete_original:
                body["delete_original"] = True
            return self._client.respond_to_url(response_url, body)

        if self._request.kind == KIND_ACTION and self._response is not None:
            return self._respond_via_action_body(text, blocks, attachments, replace_original)

        # 마지막 수단: ephemeral 메시지를 직접 게시한다.
        channel = self._request.context.get("channel_id")
        user = self._request.context.get("user_id")
        if channel and user:
            return self._client.chat_postEphemeral(
                channel=channel, user=user, text=text, blocks=blocks, attachments=attachments
            )
        raise ValueError("respond() 를 보낼 대상을 찾을 수 없습니다.")

    def _respond_via_action_body(
        self,
        text: Any,
        blocks: Sequence[dict[str, Any]] | None,
        attachments: Sequence[dict[str, Any]] | None,
        replace_original: bool,
    ) -> None:
        assert self._response is not None
        body = self._response.body if isinstance(self._response.body, dict) else {}
        rendered_attachments = self._render_attachments(blocks, attachments)
        if replace_original:
            update: dict[str, Any] = {}
            if text is not None:
                update["message"] = text
            if rendered_attachments:
                update["props"] = {"attachments": rendered_attachments}
            body["update"] = update
        else:
            if text is not None:
                body["ephemeral_text"] = text
            if rendered_attachments:
                body.setdefault("update", {})["props"] = {"attachments": rendered_attachments}
        self._response.body = body

    def _render_attachments(
        self,
        blocks: Sequence[dict[str, Any]] | None,
        attachments: Sequence[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        from ..blocks.kit import blocks_to_attachments

        rendered: list[dict[str, Any]] = []
        if blocks:
            rendered.extend(
                blocks_to_attachments(
                    blocks,
                    action_url=getattr(self._client, "action_url", ""),
                    logger=getattr(self._client, "logger", None),
                )
            )
        if attachments:
            rendered.extend(dict(a) for a in attachments)
        return rendered


def build_kwargs(
    callback: Callable[..., Any],
    *,
    request: BoltRequest,
    response: BoltResponse | None,
    client: Any,
    logger: logging.Logger,
    ack: Ack,
    next_callable: Callable[[], Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """핸들러 시그니처에 맞는 인자 dict 를 만든다.

    Args:
        extra: 호출 맥락에서만 존재하는 인자(에러 핸들러의 ``error`` 등).
    """
    event = request.body.get("event", {}) if request.kind == KIND_EVENT else {}
    channel_id = request.context.get("channel_id")
    thread_ts = event.get("thread_ts") or event.get("ts") if event else None

    say = Say(client, channel_id, None)
    # 스레드 답장을 기본으로 하지 않는다(Slack Bolt 와 동일). thread_ts 는 인자로 받는다.
    del thread_ts

    respond = Respond(client=client, request=request, response=response)

    available: dict[str, Any] = {
        "ack": ack,
        "say": say,
        "respond": respond,
        "client": client,
        "logger": logger,
        "context": request.context,
        "body": request.body,
        "payload": request.payload,
        "request": request,
        "req": request,
        "response": response,
        "resp": response,
        "next": next_callable,
        "next_": next_callable,
    }
    if extra:
        available.update(extra)

    if request.kind == KIND_EVENT:
        available["event"] = event
        if event.get("type") == "message":
            available["message"] = event
    elif request.kind == KIND_COMMAND:
        available["command"] = request.body
    elif request.kind == KIND_ACTION:
        available["action"] = request.payload
    elif request.kind == KIND_VIEW:
        available["view"] = request.body.get("view", {})
    elif request.kind == KIND_OPTIONS:
        available["options"] = request.body

    signature = _signature_of(callback)
    if signature is None:
        return available

    parameters = signature.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return available

    kwargs = {name: available[name] for name in parameters if name in available}
    missing = [
        name
        for name, param in parameters.items()
        if name not in kwargs
        and param.default is inspect.Parameter.empty
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    if missing:
        raise TypeError(
            f"{getattr(callback, '__name__', callback)!r} 의 인자 {missing} 를 주입할 수 없습니다. "
            f"사용 가능한 인자: {sorted(available)}"
        )
    return kwargs


# 콜러블 객체 자체를 키로 쓰는 약한참조 캐시.
# id() 를 키로 쓰면 함수가 GC 된 뒤 같은 주소에 다른 함수가 놓일 때
# 엉뚱한 시그니처를 돌려준다 — 실제로 테스트에서 재현된 버그다.
_signature_cache: weakref.WeakKeyDictionary[Any, inspect.Signature | None] = (
    weakref.WeakKeyDictionary()
)


def _compute_signature(callback: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(callback)
    except (TypeError, ValueError):  # pragma: no cover - 내장 함수 등
        return None


def _signature_of(callback: Callable[..., Any]) -> inspect.Signature | None:
    try:
        cached = _signature_cache.get(callback)
    except TypeError:  # pragma: no cover - 약한참조를 못 만드는 콜러블
        return _compute_signature(callback)
    if cached is None and callback not in _signature_cache:
        cached = _compute_signature(callback)
        try:
            _signature_cache[callback] = cached
        except TypeError:  # pragma: no cover
            pass
    return cached


def is_http_source(request: BoltRequest) -> bool:
    return request.source == SOURCE_HTTP
