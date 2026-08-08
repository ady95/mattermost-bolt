"""``WebClient`` 호환 파사드 — Slack Web API 메서드명을 Mattermost REST v4 로 매핑한다.

기존 Bolt 앱은 ``client.chat_postMessage(channel=..., text=...)`` 처럼 호출한다.
그 시그니처와 반환 형태를 지켜야 핸들러 본문을 수정하지 않을 수 있다.

대응물이 없는 메서드는 ``UnsupportedFeatureError`` 로 **명확히 실패**시킨다.
조용히 성공한 척하면 마이그레이션 결함이 운영 중에 드러난다.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from collections.abc import Sequence
from typing import IO, Any

import httpx

from ..blocks.kit import blocks_to_attachments, blocks_to_fallback_text, view_to_dialog
from ..blocks.mrkdwn import to_mattermost
from ..errors import MattermostApiError, UnsupportedFeatureError
from ..payload.event import post_to_message
from ..ts import TsCodec, looks_like_post_id
from .response import MattermostResponse

_logger = logging.getLogger("mattermost_bolt.client")

DEFAULT_TIMEOUT = 30.0


class WebClient:
    """Mattermost REST API v4 클라이언트 (Slack ``WebClient`` 호환 표면)."""

    def __init__(
        self,
        token: str,
        server_url: str,
        *,
        team: str | None = None,
        team_id: str | None = None,
        ts_codec: TsCodec | None = None,
        action_url: str = "",
        convert_mrkdwn: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        logger: logging.Logger | None = None,
        http_client: httpx.Client | None = None,
        verify: bool = True,
    ) -> None:
        self.token = token
        self.server_url = server_url.rstrip("/")
        self.base_url = f"{self.server_url}/api/v4"
        self.team_name = team
        self._team_id = team_id
        self.ts_codec = ts_codec or TsCodec()
        self.action_url = action_url
        self.convert_mrkdwn = convert_mrkdwn
        self.logger = logger or _logger
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout, verify=verify)
        self._channel_cache: dict[str, str] = {}
        self._user_cache: dict[str, str] = {}
        self._bot_user_id: str | None = None

    # -- 저수준 -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def api_call(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: Any = None,
        raise_on_error: bool = True,
    ) -> MattermostResponse:
        """Mattermost API 직접 호출 (탈출구)."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        response = self._http.request(
            method,
            url,
            headers=self._headers(),
            json=json_body,
            params=params,
            files=files,
            data=data,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if not isinstance(body, dict):
            body = {"items": body}
        result = MattermostResponse(
            data=body,
            status_code=response.status_code,
            api_url=url,
            headers=dict(response.headers),
        )
        if raise_on_error and not result.get("ok"):
            raise MattermostApiError(
                f"{method} {path} failed with status {response.status_code}", result
            )
        return result

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- 식별자 해석 --------------------------------------------------------

    @property
    def team_id(self) -> str | None:
        if self._team_id:
            return self._team_id
        if self.team_name:
            resp = self.api_call("GET", f"/teams/name/{self.team_name}")
            self._team_id = resp.get("id")
            return self._team_id
        # 팀이 지정되지 않았으면 봇이 속한 첫 팀을 쓴다.
        teams = self.api_call("GET", "/users/me/teams").get("items") or []
        if teams:
            if len(teams) > 1:
                self.logger.warning(
                    "봇이 %d 개 팀에 속해 있습니다. App(team=...) 으로 팀을 "
                    "지정하지 않으면 %r 을 사용합니다.",
                    len(teams),
                    teams[0].get("name"),
                )
            self._team_id = teams[0].get("id")
        return self._team_id

    def resolve_channel_id(self, channel: str) -> str:
        """채널 id / ``#name`` / ``name`` 을 채널 id 로 정규화한다."""
        if not channel:
            return channel
        if looks_like_post_id(channel):
            return channel
        name = channel.lstrip("#")
        cached = self._channel_cache.get(name)
        if cached:
            return cached
        team_id = self.team_id
        if not team_id:
            raise MattermostApiError(
                f"채널 {channel!r} 을 해석할 팀을 찾을 수 없습니다. App(team=...) 을 지정하세요."
            )
        resp = self.api_call("GET", f"/teams/{team_id}/channels/name/{name}")
        channel_id = resp.get("id", "")
        self._channel_cache[name] = channel_id
        return channel_id

    def resolve_user_id(self, user: str) -> str:
        """사용자 id / ``@name`` / ``name`` 을 사용자 id 로 정규화한다."""
        if not user:
            return user
        if looks_like_post_id(user):
            return user
        name = user.lstrip("@")
        cached = self._user_cache.get(name)
        if cached:
            return cached
        path = f"/users/email/{name}" if "@" in name else f"/users/username/{name}"
        resp = self.api_call("GET", path)
        user_id = resp.get("id", "")
        self._user_cache[name] = user_id
        return user_id

    @property
    def bot_user_id(self) -> str | None:
        if self._bot_user_id is None:
            self._bot_user_id = self.api_call("GET", "/users/me").get("id")
        return self._bot_user_id

    # -- 메시지 본문 조립 ---------------------------------------------------

    def _build_post(
        self,
        *,
        channel: str,
        text: str | None,
        blocks: Sequence[dict[str, Any]] | None,
        attachments: Sequence[dict[str, Any]] | None,
        thread_ts: str | None,
        username: str | None,
        icon_url: str | None,
        icon_emoji: str | None,
        props: dict[str, Any] | None,
        file_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        message = text or ""
        if self.convert_mrkdwn and message:
            message = to_mattermost(message)

        post_props: dict[str, Any] = dict(props or {})
        converted: list[dict[str, Any]] = []
        if blocks:
            converted = blocks_to_attachments(
                blocks,
                action_url=self.action_url,
                convert_mrkdwn=self.convert_mrkdwn,
                logger=self.logger,
            )
            if not message:
                message = blocks_to_fallback_text(blocks, convert_mrkdwn=self.convert_mrkdwn)
        if attachments:
            converted.extend(dict(a) for a in attachments)
        if converted:
            post_props["attachments"] = converted

        if username:
            post_props["override_username"] = username
        if icon_url:
            post_props["override_icon_url"] = icon_url
        if icon_emoji:
            post_props["override_icon_emoji"] = icon_emoji.strip(":")
        if username or icon_url or icon_emoji:
            post_props["from_webhook"] = "true"

        body: dict[str, Any] = {
            "channel_id": self.resolve_channel_id(channel),
            "message": message,
        }
        if thread_ts:
            body["root_id"] = self.ts_codec.decode(thread_ts)
        if post_props:
            body["props"] = post_props
        if file_ids:
            body["file_ids"] = list(file_ids)
        return body

    def _post_response(self, resp: MattermostResponse) -> MattermostResponse:
        """Mattermost post 응답을 Slack ``chat.postMessage`` 형태로 보강한다."""
        post = resp.data
        ts = self.ts_codec.encode_post(post)
        resp.data = {
            "ok": True,
            "channel": post.get("channel_id", ""),
            "ts": ts,
            "message": post_to_message(post, self.ts_codec),
            "mattermost_post": post,
        }
        return resp

    # -- chat.* -------------------------------------------------------------

    def chat_postMessage(
        self,
        *,
        channel: str,
        text: str | None = None,
        blocks: Sequence[dict[str, Any]] | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
        username: str | None = None,
        icon_url: str | None = None,
        icon_emoji: str | None = None,
        props: dict[str, Any] | None = None,
        file_ids: Sequence[str] | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        body = self._build_post(
            channel=channel,
            text=text,
            blocks=blocks,
            attachments=attachments,
            thread_ts=thread_ts,
            username=username,
            icon_url=icon_url,
            icon_emoji=icon_emoji,
            props=props,
            file_ids=file_ids,
        )
        return self._post_response(self.api_call("POST", "/posts", json_body=body))

    def chat_postEphemeral(
        self,
        *,
        channel: str,
        user: str,
        text: str | None = None,
        blocks: Sequence[dict[str, Any]] | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        post = self._build_post(
            channel=channel,
            text=text,
            blocks=blocks,
            attachments=attachments,
            thread_ts=thread_ts,
            username=None,
            icon_url=None,
            icon_emoji=None,
            props=None,
        )
        body = {"user_id": self.resolve_user_id(user), "post": post}
        resp = self.api_call("POST", "/posts/ephemeral", json_body=body)
        return self._post_response(resp)

    def chat_update(
        self,
        *,
        channel: str,
        ts: str,
        text: str | None = None,
        blocks: Sequence[dict[str, Any]] | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        props: dict[str, Any] | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        post_id = self.ts_codec.decode(ts) or ts
        body = self._build_post(
            channel=channel,
            text=text,
            blocks=blocks,
            attachments=attachments,
            thread_ts=None,
            username=None,
            icon_url=None,
            icon_emoji=None,
            props=props,
        )
        body["id"] = post_id
        body.pop("channel_id", None)
        return self._post_response(self.api_call("PUT", f"/posts/{post_id}", json_body=body))

    def chat_delete(self, *, channel: str = "", ts: str, **_ignored: Any) -> MattermostResponse:
        post_id = self.ts_codec.decode(ts) or ts
        resp = self.api_call("DELETE", f"/posts/{post_id}")
        resp.data = {"ok": True, "channel": channel, "ts": ts}
        return resp

    def chat_getPermalink(
        self, *, channel: str = "", message_ts: str, **_ignored: Any
    ) -> MattermostResponse:
        post_id = self.ts_codec.decode(message_ts) or message_ts
        team = self.team_name
        if not team:
            teams = self.api_call("GET", "/users/me/teams").get("items") or []
            team = teams[0].get("name") if teams else ""
        link = f"{self.server_url}/{team}/pl/{post_id}"
        return MattermostResponse(
            data={"ok": True, "permalink": link, "channel": channel},
            api_url=self.base_url,
        )

    # -- conversations.* ----------------------------------------------------

    def conversations_list(
        self,
        *,
        types: str = "public_channel,private_channel",
        limit: int = 200,
        cursor: str | None = None,
        team_id: str | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        tid = team_id or self.team_id
        page = int(cursor) if cursor else 0
        resp = self.api_call(
            "GET",
            f"/teams/{tid}/channels",
            params={"page": page, "per_page": limit},
        )
        raw = resp.get("items") or []
        wanted = set(types.split(","))
        channels = [
            _channel_to_conversation(c)
            for c in raw
            if _channel_type_matches(c.get("type", ""), wanted)
        ]
        resp.data = {
            "ok": True,
            "channels": channels,
            "response_metadata": {"next_cursor": str(page + 1) if len(raw) == limit else ""},
        }
        return resp

    def conversations_info(self, *, channel: str, **_ignored: Any) -> MattermostResponse:
        resp = self.api_call("GET", f"/channels/{self.resolve_channel_id(channel)}")
        resp.data = {"ok": True, "channel": _channel_to_conversation(resp.data)}
        return resp

    def conversations_open(
        self,
        *,
        users: str | Sequence[str] | None = None,
        channel: str | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        if channel:
            return self.conversations_info(channel=channel)
        if users is None:
            raise MattermostApiError("conversations_open 에는 users 가 필요합니다")
        user_list = users.split(",") if isinstance(users, str) else list(users)
        ids = [self.resolve_user_id(u) for u in user_list]
        me = self.bot_user_id
        if len(ids) == 1:
            resp = self.api_call("POST", "/channels/direct", json_body=[me, ids[0]])
        else:
            resp = self.api_call("POST", "/channels/group", json_body=[me, *ids])
        resp.data = {"ok": True, "channel": _channel_to_conversation(resp.data)}
        return resp

    def conversations_history(
        self,
        *,
        channel: str,
        limit: int = 100,
        cursor: str | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        channel_id = self.resolve_channel_id(channel)
        page = int(cursor) if cursor else 0
        resp = self.api_call(
            "GET",
            f"/channels/{channel_id}/posts",
            params={"page": page, "per_page": limit},
        )
        resp.data = self._posts_to_messages(resp.data, channel_id)
        return resp

    def conversations_replies(
        self, *, channel: str, ts: str, **_ignored: Any
    ) -> MattermostResponse:
        post_id = self.ts_codec.decode(ts) or ts
        resp = self.api_call("GET", f"/posts/{post_id}/thread")
        resp.data = self._posts_to_messages(resp.data, self.resolve_channel_id(channel))
        return resp

    def conversations_members(
        self, *, channel: str, limit: int = 200, **_ignored: Any
    ) -> MattermostResponse:
        channel_id = self.resolve_channel_id(channel)
        resp = self.api_call("GET", f"/channels/{channel_id}/members", params={"per_page": limit})
        members = [m.get("user_id") for m in (resp.get("items") or [])]
        resp.data = {"ok": True, "members": members}
        return resp

    def conversations_join(self, *, channel: str, **_ignored: Any) -> MattermostResponse:
        channel_id = self.resolve_channel_id(channel)
        resp = self.api_call(
            "POST", f"/channels/{channel_id}/members", json_body={"user_id": self.bot_user_id}
        )
        resp.data = {"ok": True, "channel": {"id": channel_id}}
        return resp

    def conversations_invite(
        self, *, channel: str, users: str | Sequence[str], **_ignored: Any
    ) -> MattermostResponse:
        channel_id = self.resolve_channel_id(channel)
        user_list = users.split(",") if isinstance(users, str) else list(users)
        for user in user_list:
            self.api_call(
                "POST",
                f"/channels/{channel_id}/members",
                json_body={"user_id": self.resolve_user_id(user)},
            )
        return MattermostResponse(
            data={"ok": True, "channel": {"id": channel_id}}, api_url=self.base_url
        )

    def _posts_to_messages(self, data: dict[str, Any], channel_id: str) -> dict[str, Any]:
        order = data.get("order") or []
        posts = data.get("posts") or {}
        messages = [
            post_to_message(posts[pid], self.ts_codec, channel_id=channel_id)
            for pid in order
            if pid in posts
        ]
        return {"ok": True, "messages": messages, "has_more": False}

    # -- users.* ------------------------------------------------------------

    def users_info(self, *, user: str, **_ignored: Any) -> MattermostResponse:
        resp = self.api_call("GET", f"/users/{self.resolve_user_id(user)}")
        resp.data = {"ok": True, "user": _user_to_slack(resp.data)}
        return resp

    def users_list(
        self, *, limit: int = 200, cursor: str | None = None, **_ignored: Any
    ) -> MattermostResponse:
        page = int(cursor) if cursor else 0
        resp = self.api_call("GET", "/users", params={"page": page, "per_page": limit})
        raw = resp.get("items") or []
        resp.data = {
            "ok": True,
            "members": [_user_to_slack(u) for u in raw],
            "response_metadata": {"next_cursor": str(page + 1) if len(raw) == limit else ""},
        }
        return resp

    def users_lookupByEmail(self, *, email: str, **_ignored: Any) -> MattermostResponse:
        resp = self.api_call("GET", f"/users/email/{email}")
        resp.data = {"ok": True, "user": _user_to_slack(resp.data)}
        return resp

    def auth_test(self, **_ignored: Any) -> MattermostResponse:
        resp = self.api_call("GET", "/users/me")
        me = resp.data
        self._bot_user_id = me.get("id")
        resp.data = {
            "ok": True,
            "url": self.server_url,
            "team": self.team_name or "",
            "user": me.get("username", ""),
            "team_id": self.team_id or "",
            "user_id": me.get("id", ""),
            "bot_id": me.get("id", ""),
            "is_bot": bool(me.get("is_bot", False)),
        }
        return resp

    # -- reactions.* --------------------------------------------------------

    def reactions_add(
        self, *, name: str, channel: str = "", timestamp: str, **_ignored: Any
    ) -> MattermostResponse:
        body = {
            "user_id": self.bot_user_id,
            "post_id": self.ts_codec.decode(timestamp) or timestamp,
            "emoji_name": name.strip(":"),
        }
        resp = self.api_call("POST", "/reactions", json_body=body)
        resp.data = {"ok": True, "channel": channel}
        return resp

    def reactions_remove(
        self, *, name: str, channel: str = "", timestamp: str, **_ignored: Any
    ) -> MattermostResponse:
        post_id = self.ts_codec.decode(timestamp) or timestamp
        resp = self.api_call(
            "DELETE",
            f"/users/{self.bot_user_id}/posts/{post_id}/reactions/{name.strip(':')}",
        )
        resp.data = {"ok": True, "channel": channel}
        return resp

    # -- files.* ------------------------------------------------------------

    def files_upload_v2(
        self,
        *,
        channel: str | None = None,
        channels: str | None = None,
        file: str | bytes | IO[bytes] | None = None,
        content: str | bytes | None = None,
        filename: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
        thread_ts: str | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        target = channel or (channels.split(",")[0] if channels else None)
        if not target:
            raise MattermostApiError("files_upload_v2 에는 channel 이 필요합니다")
        channel_id = self.resolve_channel_id(target)

        if content is not None:
            payload = content.encode("utf-8") if isinstance(content, str) else content
            name = filename or title or "upload.txt"
        elif isinstance(file, (str, os.PathLike)):
            with open(file, "rb") as fh:
                payload = fh.read()
            name = filename or os.path.basename(str(file))
        elif isinstance(file, bytes):
            payload = file
            name = filename or "upload.bin"
        elif file is not None:
            payload = file.read()
            name = filename or str(getattr(file, "name", None) or "upload.bin")
        else:
            raise MattermostApiError("files_upload_v2 에는 file 또는 content 가 필요합니다")

        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        upload = self.api_call(
            "POST",
            "/files",
            files={"files": (name, payload, mime)},
            data={"channel_id": channel_id},
        )
        infos = upload.get("file_infos") or []
        file_ids = [f.get("id") for f in infos]
        post = self.chat_postMessage(
            channel=channel_id,
            text=initial_comment or "",
            thread_ts=thread_ts,
            file_ids=file_ids,
        )
        return MattermostResponse(
            data={
                "ok": True,
                "files": infos,
                "file": infos[0] if infos else {},
                "ts": post.get("ts"),
                "channel": channel_id,
            },
            api_url=self.base_url,
        )

    # -- views.* ------------------------------------------------------------

    def views_open(
        self,
        *,
        trigger_id: str,
        view: dict[str, Any],
        dialog_url: str | None = None,
        **_ignored: Any,
    ) -> MattermostResponse:
        url = dialog_url or self.action_url
        if not url:
            raise UnsupportedFeatureError(
                "views_open 은 다이얼로그 제출을 받을 HTTP 엔드포인트가 필요합니다. "
                "App(..., mode='http', request_url=...) 로 기동하세요."
            )
        dialog, _ = view_to_dialog(
            view,
            action_url=url,
            convert_mrkdwn=self.convert_mrkdwn,
            logger=self.logger,
        )
        body = {"trigger_id": trigger_id, "url": url, "dialog": dialog}
        resp = self.api_call("POST", "/actions/dialogs/open", json_body=body)
        resp.data = {
            "ok": True,
            "view": {
                "id": dialog["callback_id"],
                "callback_id": dialog["callback_id"],
                "state": dialog["state"],
            },
        }
        return resp

    def views_update(self, **_ignored: Any) -> MattermostResponse:
        raise UnsupportedFeatureError(
            "Mattermost Interactive Dialog 는 열린 다이얼로그의 갱신을 지원하지 않습니다. "
            "제출 후 새 다이얼로그를 열거나 ephemeral 메시지로 안내하세요."
        )

    def views_push(self, **_ignored: Any) -> MattermostResponse:
        raise UnsupportedFeatureError(
            "Mattermost 에는 다이얼로그 스택(views.push)이 없습니다. "
            "단일 다이얼로그로 합치거나 단계별로 다시 열어야 합니다."
        )

    def views_publish(self, **_ignored: Any) -> MattermostResponse:
        raise UnsupportedFeatureError(
            "Mattermost 에는 Home Tab 이 없습니다. 봇 DM 채널에 메시지를 게시하세요."
        )

    # -- 편의 --------------------------------------------------------------

    def respond_to_url(self, response_url: str, body: dict[str, Any]) -> MattermostResponse:
        """``response_url`` 로 지연 응답을 보낸다."""
        response = self._http.post(
            response_url,
            headers={"Content-Type": "application/json", **self._headers()},
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        )
        return MattermostResponse(
            data={"ok": 200 <= response.status_code < 300},
            status_code=response.status_code,
            api_url=response_url,
        )


def _channel_type_matches(mm_type: str, wanted: set) -> bool:
    mapping = {
        "O": "public_channel",
        "P": "private_channel",
        "D": "im",
        "G": "mpim",
    }
    return mapping.get(mm_type, "") in wanted


def _channel_to_conversation(channel: dict[str, Any]) -> dict[str, Any]:
    mm_type = channel.get("type", "O")
    return {
        "id": channel.get("id", ""),
        "name": channel.get("name", ""),
        "name_normalized": channel.get("name", ""),
        "is_channel": mm_type == "O",
        "is_group": mm_type == "P",
        "is_im": mm_type == "D",
        "is_mpim": mm_type == "G",
        "is_private": mm_type in ("P", "D", "G"),
        "is_archived": bool(channel.get("delete_at")),
        "created": (channel.get("create_at") or 0) // 1000,
        "purpose": {"value": channel.get("purpose", "")},
        "topic": {"value": channel.get("header", "")},
        "num_members": channel.get("total_msg_count"),
        "mattermost_channel": channel,
    }


def _user_to_slack(user: dict[str, Any]) -> dict[str, Any]:
    first = user.get("first_name", "")
    last = user.get("last_name", "")
    real_name = f"{first} {last}".strip() or user.get("username", "")
    return {
        "id": user.get("id", ""),
        "name": user.get("username", ""),
        "real_name": real_name,
        "deleted": bool(user.get("delete_at")),
        "is_bot": bool(user.get("is_bot", False)),
        "is_admin": "system_admin" in (user.get("roles") or ""),
        "tz": user.get("timezone", {}).get("automaticTimezone", ""),
        "profile": {
            "real_name": real_name,
            "display_name": user.get("nickname", "") or user.get("username", ""),
            "email": user.get("email", ""),
            "first_name": first,
            "last_name": last,
            "image_72": "",
        },
        "mattermost_user": user,
    }
