"""실측 페이로드 픽스처.

Mattermost Team Edition 11.10.0 에서 ``spikes/s1_websocket.py`` /
``spikes/s2_http.py`` 로 직접 관찰한 프레임의 **형태**를 그대로 옮겼다.
식별자와 사람 이름만 익명화했다.

추측으로 만든 페이로드가 아니라는 점이 중요하다. 정규화 로직의 회귀를
막는 기준이 되려면 실제 서버가 보내는 구조와 어긋나면 안 된다.
"""

from __future__ import annotations

import json
from typing import Any

TEAM_ID = "eyb3z86cdjnop8f8bnyzkpesxa"
CHANNEL_ID = "uem5sm8xb3dgmebs75pdjetzec"
USER_ID = "n6f3pcmoip83xpr44eot1huuor"
BOT_USER_ID = "b3axygf55frrtjhnrcaqu8sj3e"
POST_ID = "kb17n49ptj8gzx8at13t5edbia"
ROOT_POST_ID = "achaii7pkjfkmy3nm6yxs8w71e"
CREATE_AT = 1786160418395


def _post(**overrides: Any) -> dict[str, Any]:
    post = {
        "id": POST_ID,
        "create_at": CREATE_AT,
        "update_at": CREATE_AT,
        "edit_at": 0,
        "delete_at": 0,
        "is_pinned": False,
        "user_id": USER_ID,
        "channel_id": CHANNEL_ID,
        "root_id": "",
        "original_id": "",
        "message": "spike message *bold* 한글",
        "type": "",
        "props": {},
        "hashtags": "",
        "file_ids": [],
        "pending_post_id": "",
        "remote_id": "",
        "reply_count": 0,
        "last_reply_at": 0,
        "participants": None,
        "metadata": {},
    }
    post.update(overrides)
    return post


def ws_posted(**post_overrides: Any) -> dict[str, Any]:
    """``posted`` 프레임. ``data.post`` 가 JSON **문자열**인 점이 핵심이다."""
    return {
        "event": "posted",
        "data": {
            "channel_display_name": "Bolt Dev",
            "channel_name": "bolt-dev",
            "channel_type": "O",
            "post": json.dumps(_post(**post_overrides)),
            "sender_name": "@admin",
            "set_online": True,
            "team_id": TEAM_ID,
        },
        "broadcast": {
            "omit_users": None,
            "user_id": "",
            "channel_id": CHANNEL_ID,
            "team_id": "",
            "connection_id": "",
            "omit_connection_id": "",
        },
        "seq": 2,
    }


def ws_post_edited() -> dict[str, Any]:
    """``post_edited`` — ``data`` 에 channel_name/channel_type 이 없다."""
    return {
        "event": "post_edited",
        "data": {
            "post": json.dumps(
                _post(
                    message="spike message edited",
                    edit_at=CREATE_AT + 120,
                    update_at=CREATE_AT + 120,
                    reply_count=1,
                )
            )
        },
        "broadcast": {
            "omit_users": None,
            "user_id": "",
            "channel_id": CHANNEL_ID,
            "team_id": "",
        },
        "seq": 5,
    }


def ws_post_deleted() -> dict[str, Any]:
    return {
        "event": "post_deleted",
        "data": {"post": json.dumps(_post(delete_at=CREATE_AT + 200))},
        "broadcast": {"channel_id": CHANNEL_ID, "team_id": "", "user_id": ""},
        "seq": 6,
    }


def ws_reaction_added() -> dict[str, Any]:
    """``reaction_added`` — ``data`` 에 channel_id 가 없어 broadcast 를 봐야 한다."""
    return {
        "event": "reaction_added",
        "data": {
            "reaction": json.dumps(
                {
                    "user_id": USER_ID,
                    "post_id": POST_ID,
                    "emoji_name": "+1",
                    "create_at": CREATE_AT + 108,
                    "update_at": CREATE_AT + 108,
                    "delete_at": 0,
                    "remote_id": "",
                    "channel_id": CHANNEL_ID,
                }
            )
        },
        "broadcast": {
            "omit_users": None,
            "user_id": "",
            "channel_id": CHANNEL_ID,
            "team_id": "",
        },
        "seq": 4,
    }


def ws_hello() -> dict[str, Any]:
    return {
        "event": "hello",
        "data": {"connection_id": "abc", "server_version": "11.10.0"},
        "broadcast": {"channel_id": "", "team_id": "", "user_id": ""},
        "seq": 1,
    }


def http_command_form(text: str = "hello world") -> dict[str, str]:
    """slash command 는 ``application/x-www-form-urlencoded`` 로 온다."""
    return {
        "channel_id": CHANNEL_ID,
        "channel_name": "bolt-dev",
        "command": "/boltspike",
        "response_url": "https://mattermost.example.com/hooks/commands/xxxxxxxx",
        "team_domain": "bolt",
        "team_id": TEAM_ID,
        "text": text,
        "token": "cmdtoken000000000000000000",
        "trigger_id": "dHJpZ2dlcl9pZF9leGFtcGxl",
        "user_id": USER_ID,
        "user_name": "admin",
    }


def http_action_body(**context_overrides: Any) -> dict[str, Any]:
    """버튼 클릭 시 Mattermost 가 통합 URL 로 보내는 원본 본문."""
    context = {
        "action_id": "spike_button",
        "block_id": "spike_block",
        "value": "spike-value",
        "type": "button",
    }
    context.update(context_overrides)
    return {
        "user_id": USER_ID,
        "user_name": "admin",
        "channel_id": CHANNEL_ID,
        "channel_name": "bolt-dev",
        "team_id": TEAM_ID,
        "team_domain": "bolt",
        "post_id": POST_ID,
        "trigger_id": "dHJpZ2dlcl9pZF9leGFtcGxl",
        "type": "button",
        "data_source": "",
        "context": context,
    }


def http_dialog_body(
    *, cancelled: bool = False, state: str = "", submission: dict[str, Any] | None = None
) -> dict[str, Any]:
    """다이얼로그 제출 원본 본문."""
    return {
        "type": "dialog_submission",
        "callback_id": "spike_dialog",
        "state": state,
        "user_id": USER_ID,
        "channel_id": CHANNEL_ID,
        "team_id": TEAM_ID,
        "submission": submission if submission is not None else {"e0": "홍길동"},
        "cancelled": cancelled,
    }
