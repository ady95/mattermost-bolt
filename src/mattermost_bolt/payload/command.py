"""Slash command 페이로드 정규화.

Mattermost 의 slash command POST 필드는 Slack 과 사실상 동일하다
(실행계획서 §3.4). 이 구간이 마이그레이션 비용이 가장 낮다.

Slack 에만 있는 필드(``api_app_id``, ``enterprise_id``)만 보정한다.
"""

from __future__ import annotations

import shlex
from typing import Any

DEFAULT_API_APP_ID = "mattermost-bolt"

# Mattermost 가 보내는 필드 그대로 통과시키는 목록.
_PASSTHROUGH = (
    "token",
    "team_id",
    "team_domain",
    "channel_id",
    "channel_name",
    "user_id",
    "user_name",
    "command",
    "text",
    "response_url",
    "trigger_id",
)


def normalize_command(
    form: dict[str, Any], *, api_app_id: str = DEFAULT_API_APP_ID
) -> dict[str, Any]:
    """Mattermost slash command 폼 → Slack command 페이로드."""
    payload: dict[str, Any] = {key: form.get(key, "") for key in _PASSTHROUGH}
    command = payload.get("command") or ""
    if command and not command.startswith("/"):
        payload["command"] = f"/{command}"
    payload["api_app_id"] = api_app_id
    payload["enterprise_id"] = None
    payload["enterprise_name"] = None
    payload["is_enterprise_install"] = False
    payload["user"] = {
        "id": payload.get("user_id", ""),
        "username": payload.get("user_name", ""),
    }
    payload["channel"] = {
        "id": payload.get("channel_id", ""),
        "name": payload.get("channel_name", ""),
    }
    return payload


def build_command_from_message(
    text: str,
    *,
    event: dict[str, Any],
    api_app_id: str = DEFAULT_API_APP_ID,
) -> dict[str, Any] | None:
    """메시지 본문에서 slash command 를 합성한다 (Pseudo Socket Mode, 결정 D3).

    HTTP 리시버 없이 동작해야 하는 개발·폐쇄망 환경을 위해,
    WebSocket 으로 받은 ``/cmd args`` 텍스트를 command 페이로드로 만든다.
    Mattermost 가 발급하는 ``trigger_id`` / ``response_url`` 은 이 경로에서
    존재하지 않는다. 다이얼로그를 여는 핸들러라면 http 모드가 필요하다.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    first, _, rest = stripped.partition(" ")
    return {
        "token": "",
        "team_id": event.get("team", ""),
        "team_domain": "",
        "channel_id": event.get("channel", ""),
        "channel_name": event.get("channel_name", ""),
        "user_id": event.get("user", ""),
        "user_name": event.get("user_name", ""),
        "command": first,
        "text": rest.strip(),
        "response_url": "",
        "trigger_id": "",
        "api_app_id": api_app_id,
        "enterprise_id": None,
        "is_enterprise_install": False,
        "user": {"id": event.get("user", ""), "username": event.get("user_name", "")},
        "channel": {
            "id": event.get("channel", ""),
            "name": event.get("channel_name", ""),
        },
        "mattermost_source": "websocket",
    }


def split_args(text: str) -> tuple[str, ...]:
    """command 인자를 셸 규칙으로 분해한다 (따옴표 지원)."""
    try:
        return tuple(shlex.split(text or ""))
    except ValueError:
        return tuple((text or "").split())
