"""Mattermost WebSocket 이벤트 → Slack Events API 형태로 정규화.

기존 Bolt 앱의 핸들러는 ``message["text"]``, ``message["user"]``,
``message["channel"]``, ``event["ts"]`` 를 읽는다. 그 계약을 그대로 지킨다.

Mattermost ``posted`` 이벤트의 원본 형태::

    {
      "event": "posted",
      "data": {
        "channel_display_name": "Bolt Dev",
        "channel_name": "bolt-dev",
        "channel_type": "O",
        "post": "{...JSON 문자열...}",     ← 문자열이다. 파싱이 필요하다.
        "sender_name": "@carol",
        "team_id": "..."
      },
      "broadcast": {"channel_id": "...", "team_id": "", "user_id": ""},
      "seq": 7
    }
"""

from __future__ import annotations

import json
from typing import Any

from ..ts import TsCodec

# Mattermost 채널 타입 → Slack channel_type
CHANNEL_TYPE_MAP = {
    "O": "channel",  # public
    "P": "group",  # private
    "D": "im",  # direct
    "G": "mpim",  # group direct
}

# Mattermost WS 이벤트 → Slack 이벤트 타입.
EVENT_TYPE_MAP = {
    "posted": "message",
    "post_edited": "message",
    "post_deleted": "message",
    "reaction_added": "reaction_added",
    "reaction_removed": "reaction_removed",
    "user_added": "member_joined_channel",
    "user_removed": "member_left_channel",
    "channel_created": "channel_created",
    "channel_deleted": "channel_deleted",
    "direct_added": "message",
    "typing": "user_typing",
    "status_change": "user_change",
}

# Mattermost 시스템 post type → Slack message subtype.
SYSTEM_SUBTYPE_MAP = {
    "system_join_channel": "channel_join",
    "system_leave_channel": "channel_leave",
    "system_add_to_channel": "channel_join",
    "system_remove_from_channel": "channel_leave",
    "system_header_change": "channel_topic",
    "system_displayname_change": "channel_name",
    "system_purpose_change": "channel_purpose",
}

DEFAULT_API_APP_ID = "mattermost-bolt"


def parse_post(data: dict[str, Any]) -> dict[str, Any]:
    """``data.post`` 는 JSON 문자열이다. dict 로 돌려준다."""
    post = data.get("post")
    if isinstance(post, str):
        try:
            return json.loads(post)
        except (TypeError, ValueError):
            return {}
    return post or {}


def post_to_message(
    post: dict[str, Any],
    codec: TsCodec,
    *,
    channel_type: str = "O",
    channel_id: str | None = None,
    team_id: str | None = None,
    subtype: str | None = None,
) -> dict[str, Any]:
    """Mattermost post → Slack message 이벤트 dict."""
    ts = codec.encode_post(post)
    message: dict[str, Any] = {
        "type": "message",
        "channel": channel_id or post.get("channel_id", ""),
        "user": post.get("user_id", ""),
        "text": post.get("message", ""),
        "ts": ts,
        "event_ts": ts,
        "channel_type": CHANNEL_TYPE_MAP.get(channel_type, "channel"),
    }
    if team_id:
        message["team"] = team_id

    root_id = post.get("root_id") or ""
    if root_id:
        message["thread_ts"] = codec.encode(root_id)
        message["parent_user_id"] = post.get("props", {}).get("root_user_id", "")

    props = post.get("props") or {}
    if props.get("from_bot") in ("true", True) or props.get("from_webhook") in (
        "true",
        True,
    ):
        # Slack 앱은 봇 메시지를 bot_id 유무로 판별한다. 그 관례를 지킨다.
        message["bot_id"] = props.get("override_username") or post.get("user_id", "")
        message["subtype"] = "bot_message"
        if props.get("override_username"):
            message["username"] = props["override_username"]

    explicit_subtype = subtype or SYSTEM_SUBTYPE_MAP.get(post.get("type", ""))
    if explicit_subtype:
        message["subtype"] = explicit_subtype

    files = (post.get("metadata") or {}).get("files") or []
    if files:
        message["files"] = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "mimetype": f.get("mime_type"),
                "size": f.get("size"),
            }
            for f in files
        ]
    if post.get("edit_at"):
        message["edited"] = {"user": post.get("user_id", ""), "ts": ts}

    # 원본 접근이 필요한 앱을 위해 Mattermost post 를 통째로 보존한다.
    message["mattermost_post"] = post
    return message


def _event_envelope(
    event: dict[str, Any],
    *,
    team_id: str,
    api_app_id: str,
    event_id: str,
    event_time: int = 0,
) -> dict[str, Any]:
    """Slack Events API 의 ``event_callback`` 봉투.

    ``event_time`` 은 초 단위 epoch 다. ``ts_format="post_id"`` 에서는
    ``event_ts`` 가 post id 이므로 여기서 파생시킬 수 없다. 호출자가
    Mattermost ``create_at`` 에서 직접 넘긴다.
    """
    return {
        "token": "",
        "team_id": team_id,
        "api_app_id": api_app_id,
        "event": event,
        "type": "event_callback",
        "event_id": event_id,
        "event_time": event_time,
        "authorizations": [],
    }


def normalize_ws_event(
    raw: dict[str, Any],
    codec: TsCodec,
    *,
    api_app_id: str = DEFAULT_API_APP_ID,
) -> dict[str, Any] | None:
    """Mattermost WS 프레임 → Slack ``event_callback`` 봉투.

    대응되는 Slack 이벤트가 없으면 ``None`` 을 돌려준다.
    """
    ws_event = raw.get("event")
    if not ws_event:
        return None

    data: dict[str, Any] = raw.get("data") or {}
    broadcast: dict[str, Any] = raw.get("broadcast") or {}
    channel_id = broadcast.get("channel_id") or data.get("channel_id") or ""
    team_id = data.get("team_id") or broadcast.get("team_id") or ""
    seq = str(raw.get("seq", ""))

    if ws_event in ("posted", "post_edited", "post_deleted", "direct_added"):
        post = parse_post(data)
        if not post:
            return None
        subtype = None
        if ws_event == "post_edited":
            subtype = "message_changed"
        elif ws_event == "post_deleted":
            subtype = "message_deleted"
        event = post_to_message(
            post,
            codec,
            channel_type=data.get("channel_type", "O"),
            channel_id=channel_id or post.get("channel_id"),
            team_id=team_id,
            subtype=subtype,
        )
        if data.get("sender_name"):
            event["user_name"] = str(data["sender_name"]).lstrip("@")
        if data.get("channel_name"):
            event["channel_name"] = data["channel_name"]
        return _event_envelope(
            event,
            team_id=team_id,
            api_app_id=api_app_id,
            event_id=f"Ev{seq}",
            event_time=(post.get("create_at") or 0) // 1000,
        )

    slack_type = EVENT_TYPE_MAP.get(ws_event)
    if slack_type is None:
        return None

    # 메시지 계열이 아닌 이벤트에는 대응되는 post 가 없다. Slack 은 ``event_ts`` 를
    # 요구하므로 WebSocket 시퀀스 번호를 불투명 식별자로 채운다.
    event = {
        "type": slack_type,
        "channel": channel_id,
        "event_ts": seq,
        "mattermost_event": ws_event,
        "mattermost_data": data,
    }
    event_time = 0

    if slack_type in ("reaction_added", "reaction_removed"):
        reaction = data.get("reaction")
        if isinstance(reaction, str):
            try:
                reaction = json.loads(reaction)
            except (TypeError, ValueError):
                reaction = {}
        reaction = reaction or {}
        event_time = (reaction.get("create_at") or 0) // 1000
        event["user"] = reaction.get("user_id", "")
        event["reaction"] = reaction.get("emoji_name", "")
        event["item"] = {
            "type": "message",
            "channel": channel_id,
            "ts": codec.decode(reaction.get("post_id", "")) or reaction.get("post_id"),
        }
        event["item_user"] = ""
    elif slack_type in ("member_joined_channel", "member_left_channel"):
        event["user"] = data.get("user_id", "")
        event["channel_type"] = "C"
        event["inviter"] = data.get("remover_id") or ""
    elif slack_type == "user_typing":
        event["user"] = data.get("user_id", "")

    return _event_envelope(
        event,
        team_id=team_id,
        api_app_id=api_app_id,
        event_id=f"Ev{seq}",
        event_time=event_time,
    )
