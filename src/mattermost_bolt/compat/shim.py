"""``sys.modules`` 에 ``slack_bolt`` / ``slack_sdk`` 를 등록하는 shim."""

from __future__ import annotations

import logging
import sys
import types

from ..app import App
from ..context import BoltContext
from ..errors import BoltError, MattermostApiError
from ..listener.args import Ack, Respond, Say
from ..request import BoltRequest, BoltResponse
from ..web.client import WebClient
from ..web.response import MattermostResponse

_logger = logging.getLogger("mattermost_bolt.compat")

# install() 이 만든 모듈 이름 — uninstall() 에서 되돌린다.
_INSTALLED: list[str] = []
_ORIGINALS: dict[str, types.ModuleType] = {}


def _module(name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__doc__ = f"mattermost-bolt 가 제공하는 {name} 호환 모듈"
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def install(*, strict: bool = False) -> None:
    """``slack_bolt`` / ``slack_sdk`` import 를 mattermost-bolt 로 돌린다.

    Args:
        strict: 진짜 ``slack_bolt`` 가 이미 import 되어 있으면 예외를 낸다.
            기본값은 경고만 남기고 덮어쓴다.
    """
    from ..adapter.socket_mode import SocketModeHandler

    for name in ("slack_bolt", "slack_sdk"):
        existing = sys.modules.get(name)
        if existing is not None and not getattr(existing, "__mmbolt_shim__", False):
            message = (
                f"{name} 이 이미 import 되어 있습니다. shim 이 이를 덮어씁니다. "
                f"install() 을 다른 import 보다 먼저 호출하세요."
            )
            if strict:
                raise BoltError(message)
            _logger.warning(message)
            _ORIGINALS[name] = existing

    slack_bolt = _module(
        "slack_bolt",
        App=App,
        AsyncApp=App,
        BoltContext=BoltContext,
        BoltRequest=BoltRequest,
        BoltResponse=BoltResponse,
        Ack=Ack,
        Say=Say,
        Respond=Respond,
        Args=BoltContext,
        __mmbolt_shim__=True,
    )
    slack_bolt_adapter = _module("slack_bolt.adapter", __mmbolt_shim__=True)
    socket_mode = _module(
        "slack_bolt.adapter.socket_mode",
        SocketModeHandler=SocketModeHandler,
        __mmbolt_shim__=True,
    )
    slack_bolt_error = _module("slack_bolt.error", BoltError=BoltError, __mmbolt_shim__=True)
    slack_bolt_adapter.socket_mode = socket_mode  # type: ignore[attr-defined]
    slack_bolt.adapter = slack_bolt_adapter  # type: ignore[attr-defined]
    slack_bolt.error = slack_bolt_error  # type: ignore[attr-defined]

    slack_sdk = _module("slack_sdk", WebClient=WebClient, __mmbolt_shim__=True)
    slack_sdk_errors = _module(
        "slack_sdk.errors",
        SlackApiError=MattermostApiError,
        SlackClientError=MattermostApiError,
        __mmbolt_shim__=True,
    )
    slack_sdk_web = _module(
        "slack_sdk.web",
        WebClient=WebClient,
        SlackResponse=MattermostResponse,
        __mmbolt_shim__=True,
    )
    slack_sdk.errors = slack_sdk_errors  # type: ignore[attr-defined]
    slack_sdk.web = slack_sdk_web  # type: ignore[attr-defined]

    modules = {
        "slack_bolt": slack_bolt,
        "slack_bolt.adapter": slack_bolt_adapter,
        "slack_bolt.adapter.socket_mode": socket_mode,
        "slack_bolt.error": slack_bolt_error,
        "slack_sdk": slack_sdk,
        "slack_sdk.errors": slack_sdk_errors,
        "slack_sdk.web": slack_sdk_web,
    }
    sys.modules.update(modules)
    _INSTALLED.clear()
    _INSTALLED.extend(modules)
    _logger.info("slack_bolt / slack_sdk import 를 mattermost-bolt 로 대체했습니다.")


def uninstall() -> None:
    """``install()`` 을 되돌린다."""
    for name in _INSTALLED:
        sys.modules.pop(name, None)
    sys.modules.update(_ORIGINALS)
    _INSTALLED.clear()
    _ORIGINALS.clear()
