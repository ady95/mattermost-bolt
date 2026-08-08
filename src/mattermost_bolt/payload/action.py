"""인터랙티브 페이로드 정규화.

- attachment action(버튼·셀렉트) → Slack ``block_actions``
- dialog submission            → Slack ``view_submission``
- dialog cancel                → Slack ``view_closed``

Mattermost 는 액션 클릭 시 ``integration.context`` 를 그대로 되돌려 준다.
Block Kit 변환기가 그 안에 ``action_id`` / ``block_id`` 를 심어두므로
(``blocks/kit.py`` 참조) Slack 의 식별자 체계를 왕복 보존할 수 있다.
"""

from __future__ import annotations

import json
from typing import Any

from ..blocks.kit import dialog_submission_to_view_state
from ..ts import TsCodec

DEFAULT_API_APP_ID = "mattermost-bolt"


def normalize_action(
    body: dict[str, Any],
    codec: TsCodec,
    *,
    api_app_id: str = DEFAULT_API_APP_ID,
) -> dict[str, Any]:
    """Mattermost interactive action → Slack ``block_actions`` 페이로드."""
    context: dict[str, Any] = body.get("context") or {}
    action_id = context.get("action_id") or body.get("action_id") or ""
    block_id = context.get("block_id") or ""
    post_id = body.get("post_id", "")
    ts = codec.ts_for(post_id) or post_id

    action: dict[str, Any] = {
        "action_id": action_id,
        "block_id": block_id,
        "action_ts": ts,
        "type": _action_type(body, context),
    }

    selected = context.get("selected_option")
    if selected is not None:
        action["selected_option"] = {
            "value": selected,
            "text": {"type": "plain_text", "text": str(selected)},
        }
        action["value"] = selected
    else:
        action["value"] = context.get("value")

    return {
        "type": "block_actions",
        "user": {
            "id": body.get("user_id", ""),
            "username": body.get("user_name", ""),
            "name": body.get("user_name", ""),
        },
        "api_app_id": api_app_id,
        "token": "",
        "container": {
            "type": "message",
            "message_ts": ts,
            "channel_id": body.get("channel_id", ""),
            "is_ephemeral": False,
        },
        "trigger_id": body.get("trigger_id", ""),
        "team": {
            "id": body.get("team_id", ""),
            "domain": body.get("team_domain", ""),
        },
        "channel": {
            "id": body.get("channel_id", ""),
            "name": body.get("channel_name", ""),
        },
        "message": {"ts": ts, "channel": body.get("channel_id", "")},
        "response_url": "",
        "actions": [action],
        "mattermost_context": context,
        "mattermost_post_id": post_id,
    }


def _action_type(body: dict[str, Any], context: dict[str, Any]) -> str:
    ctx_type = context.get("type")
    if ctx_type:
        return ctx_type
    if body.get("data_source") or context.get("selected_option") is not None:
        return "static_select"
    return "button"


def normalize_dialog(
    body: dict[str, Any],
    *,
    api_app_id: str = DEFAULT_API_APP_ID,
) -> dict[str, Any]:
    """Mattermost dialog 제출/취소 → Slack ``view_submission`` / ``view_closed``."""
    state_meta = _parse_state(body.get("state"))
    submission = body.get("submission") or {}
    cancelled = bool(body.get("cancelled"))
    callback_id = body.get("callback_id") or state_meta.get("cb", "")

    view = {
        "id": callback_id,
        "type": "modal",
        "callback_id": callback_id,
        "private_metadata": state_meta.get("pm", ""),
        "state": {"values": dialog_submission_to_view_state(submission, state_meta)},
        "title": {"type": "plain_text", "text": callback_id},
        "hash": "",
        "mattermost_state": body.get("state", ""),
        "mattermost_submission": submission,
    }

    return {
        "type": "view_closed" if cancelled else "view_submission",
        "team": {"id": body.get("team_id", ""), "domain": ""},
        "user": {
            "id": body.get("user_id", ""),
            "username": body.get("user_name", ""),
            "name": body.get("user_name", ""),
        },
        "api_app_id": api_app_id,
        "token": "",
        "trigger_id": body.get("trigger_id", ""),
        "view": view,
        "response_urls": [],
        "is_cleared": cancelled,
        "channel": {"id": body.get("channel_id", "")},
        "mattermost_channel_id": body.get("channel_id", ""),
    }


def _parse_state(state: Any) -> dict[str, Any]:
    if isinstance(state, dict):
        return state
    if isinstance(state, str) and state:
        try:
            parsed = json.loads(state)
        except (TypeError, ValueError):
            # 수동으로 만든 다이얼로그는 임의 문자열을 state 로 쓴다.
            return {"pm": state, "cb": "", "map": {}}
        if isinstance(parsed, dict):
            return parsed
    return {"pm": "", "cb": "", "map": {}}


def errors_to_dialog_response(
    errors: dict[str, str], state_meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Slack ``ack(response_action="errors", errors=...)`` → dialog 오류 응답.

    Slack 은 ``block_id`` 를 키로 쓰고 Mattermost 는 element ``name`` 을 쓰므로
    ``state`` 의 매핑을 역방향으로 조회한다.
    """
    mapping = (state_meta or {}).get("map", {}) or {}
    reverse = {value[0]: name for name, value in mapping.items() if value}
    return {"errors": {reverse.get(key, key): message for key, message in errors.items()}}
