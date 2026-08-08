"""WebClient — Slack 메서드명 → Mattermost REST v4 매핑."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from mattermost_bolt.errors import MattermostApiError, UnsupportedFeatureError
from mattermost_bolt.ts import TsCodec
from mattermost_bolt.web.client import WebClient

BASE = "http://mm.test/api/v4"
CHANNEL_ID = "uem5sm8xb3dgmebs75pdjetzec"
USER_ID = "n6f3pcmoip83xpr44eot1huuor"
POST_ID = "kb17n49ptj8gzx8at13t5edbia"


def make_post(**overrides: Any) -> dict[str, Any]:
    post = {
        "id": POST_ID,
        "create_at": 1786160418395,
        "update_at": 1786160418395,
        "edit_at": 0,
        "user_id": USER_ID,
        "channel_id": CHANNEL_ID,
        "root_id": "",
        "message": "hi",
        "type": "",
        "props": {},
        "metadata": {},
    }
    post.update(overrides)
    return post


@pytest.fixture
def client() -> WebClient:
    return WebClient(
        token="t" * 26,
        server_url="http://mm.test",
        team="bolt",
        ts_codec=TsCodec(),
        action_url="http://app.test/mmbolt/actions",
    )


@respx.mock
def test_chat_post_message_maps_to_posts(client: WebClient) -> None:
    route = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))
    response = client.chat_postMessage(channel=CHANNEL_ID, text="hi")

    assert route.called
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {'t' * 26}"
    body = _json(route)
    assert body == {"channel_id": CHANNEL_ID, "message": "hi"}
    # Slack 응답 형태로 되돌아온다.
    assert response["ok"] is True
    assert response["ts"] == POST_ID
    assert response["channel"] == CHANNEL_ID
    assert response["message"]["text"] == "hi"


@respx.mock
def test_mrkdwn_is_converted_on_the_way_out(client: WebClient) -> None:
    route = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))
    client.chat_postMessage(channel=CHANNEL_ID, text="*굵게* <https://x.test|링크>")
    assert _json(route)["message"] == "**굵게** [링크](https://x.test)"


@respx.mock
def test_blocks_become_attachment_props(client: WebClient) -> None:
    route = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))
    client.chat_postMessage(
        channel=CHANNEL_ID,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "*본문*"}},
            {
                "type": "actions",
                "block_id": "b1",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "go",
                        "text": {"type": "plain_text", "text": "Go"},
                    }
                ],
            },
        ],
    )
    attachments = _json(route)["props"]["attachments"]
    assert attachments[0]["text"] == "**본문**"
    assert attachments[0]["actions"][0]["integration"]["url"] == ("http://app.test/mmbolt/actions")
    # blocks 만 준 경우 알림용 폴백 텍스트가 채워진다.
    assert _json(route)["message"] == "**본문**"


@respx.mock
def test_thread_ts_becomes_root_id(client: WebClient) -> None:
    route = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))
    client.chat_postMessage(channel=CHANNEL_ID, text="reply", thread_ts=POST_ID)
    assert _json(route)["root_id"] == POST_ID


@respx.mock
def test_username_override_sets_props(client: WebClient) -> None:
    route = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))
    client.chat_postMessage(channel=CHANNEL_ID, text="x", username="deploy", icon_emoji=":rocket:")
    props = _json(route)["props"]
    assert props["override_username"] == "deploy"
    assert props["override_icon_emoji"] == "rocket"
    assert props["from_webhook"] == "true"


@respx.mock
def test_channel_name_is_resolved_to_id(client: WebClient) -> None:
    respx.get(f"{BASE}/teams/name/bolt").mock(
        return_value=httpx.Response(200, json={"id": "team00000000000000000000a"})
    )
    lookup = respx.get(f"{BASE}/teams/team00000000000000000000a/channels/name/bolt-dev").mock(
        return_value=httpx.Response(200, json={"id": CHANNEL_ID})
    )
    post = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))

    client.chat_postMessage(channel="#bolt-dev", text="hi")
    assert _json(post)["channel_id"] == CHANNEL_ID

    # 두 번째 호출은 캐시를 쓴다 — 매 메시지마다 조회하지 않는다.
    client.chat_postMessage(channel="#bolt-dev", text="hi again")
    assert lookup.call_count == 1


@respx.mock
def test_chat_update(client: WebClient) -> None:
    route = respx.put(f"{BASE}/posts/{POST_ID}").mock(
        return_value=httpx.Response(200, json=make_post(message="edited"))
    )
    response = client.chat_update(channel=CHANNEL_ID, ts=POST_ID, text="edited")
    assert _json(route)["id"] == POST_ID
    assert response["ts"] == POST_ID


@respx.mock
def test_chat_delete(client: WebClient) -> None:
    route = respx.delete(f"{BASE}/posts/{POST_ID}").mock(
        return_value=httpx.Response(200, json={"status": "OK"})
    )
    response = client.chat_delete(channel=CHANNEL_ID, ts=POST_ID)
    assert route.called
    assert response["ok"] is True


@respx.mock
def test_chat_post_ephemeral(client: WebClient) -> None:
    route = respx.post(f"{BASE}/posts/ephemeral").mock(
        return_value=httpx.Response(200, json=make_post())
    )
    client.chat_postEphemeral(channel=CHANNEL_ID, user=USER_ID, text="너만 봐")
    body = _json(route)
    assert body["user_id"] == USER_ID
    assert body["post"]["message"] == "너만 봐"


@respx.mock
def test_reactions_add(client: WebClient) -> None:
    respx.get(f"{BASE}/users/me").mock(
        return_value=httpx.Response(200, json={"id": "bot0000000000000000000000"})
    )
    route = respx.post(f"{BASE}/reactions").mock(
        return_value=httpx.Response(201, json={"emoji_name": "+1"})
    )
    client.reactions_add(name=":+1:", channel=CHANNEL_ID, timestamp=POST_ID)
    body = _json(route)
    assert body["emoji_name"] == "+1"  # 콜론이 제거된다
    assert body["post_id"] == POST_ID


@respx.mock
def test_users_info_returns_slack_shape(client: WebClient) -> None:
    respx.get(f"{BASE}/users/{USER_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": USER_ID,
                "username": "carol",
                "first_name": "Carol",
                "last_name": "Park",
                "email": "carol@test.local",
                "roles": "system_user",
                "is_bot": False,
                "delete_at": 0,
            },
        )
    )
    user = client.users_info(user=USER_ID)["user"]
    assert user["id"] == USER_ID
    assert user["name"] == "carol"
    assert user["real_name"] == "Carol Park"
    assert user["profile"]["email"] == "carol@test.local"
    assert user["is_admin"] is False


@respx.mock
def test_conversations_history_returns_messages(client: WebClient) -> None:
    respx.get(f"{BASE}/channels/{CHANNEL_ID}/posts").mock(
        return_value=httpx.Response(200, json={"order": [POST_ID], "posts": {POST_ID: make_post()}})
    )
    messages = client.conversations_history(channel=CHANNEL_ID)["messages"]
    assert len(messages) == 1
    assert messages[0]["ts"] == POST_ID
    assert messages[0]["text"] == "hi"


@respx.mock
def test_conversations_list_filters_by_type(client: WebClient) -> None:
    respx.get(f"{BASE}/teams/name/bolt").mock(
        return_value=httpx.Response(200, json={"id": "team00000000000000000000a"})
    )
    respx.get(f"{BASE}/teams/team00000000000000000000a/channels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "c1", "name": "public", "type": "O", "create_at": 0},
                {"id": "c2", "name": "private", "type": "P", "create_at": 0},
            ],
        )
    )
    channels = client.conversations_list(types="public_channel")["channels"]
    assert [c["name"] for c in channels] == ["public"]
    assert channels[0]["is_channel"] is True


@respx.mock
def test_auth_test(client: WebClient) -> None:
    respx.get(f"{BASE}/users/me").mock(
        return_value=httpx.Response(
            200, json={"id": "bot0000000000000000000000", "username": "boltbot", "is_bot": True}
        )
    )
    respx.get(f"{BASE}/teams/name/bolt").mock(
        return_value=httpx.Response(200, json={"id": "team00000000000000000000a"})
    )
    auth = client.auth_test()
    assert auth["user"] == "boltbot"
    assert auth["user_id"] == "bot0000000000000000000000"
    assert auth["is_bot"] is True


@respx.mock
def test_views_open_sends_dialog(client: WebClient) -> None:
    route = respx.post(f"{BASE}/actions/dialogs/open").mock(
        return_value=httpx.Response(200, json={"status": "OK"})
    )
    client.views_open(
        trigger_id="trig",
        view={
            "type": "modal",
            "callback_id": "cb",
            "title": {"type": "plain_text", "text": "T"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "b1",
                    "label": {"type": "plain_text", "text": "이름"},
                    "element": {"type": "plain_text_input", "action_id": "name"},
                }
            ],
        },
        dialog_url="http://app.test/mmbolt/dialogs",
    )
    body = _json(route)
    assert body["trigger_id"] == "trig"
    assert body["dialog"]["callback_id"] == "cb"
    assert body["dialog"]["elements"][0]["name"] == "e0"


def test_views_open_without_url_is_a_clear_error(client: WebClient) -> None:
    client.action_url = ""
    with pytest.raises(UnsupportedFeatureError, match="HTTP 엔드포인트"):
        client.views_open(trigger_id="t", view={"type": "modal"})


@pytest.mark.parametrize("method", ["views_update", "views_push", "views_publish"])
def test_unsupported_methods_fail_loudly(client: WebClient, method: str) -> None:
    """조용히 성공한 척하면 결함이 운영에서 드러난다."""
    with pytest.raises(UnsupportedFeatureError):
        getattr(client, method)()


@respx.mock
def test_api_error_carries_response(client: WebClient) -> None:
    respx.post(f"{BASE}/posts").mock(
        return_value=httpx.Response(
            403, json={"message": "권한 없음", "id": "api.context.permissions.app_error"}
        )
    )
    with pytest.raises(MattermostApiError) as excinfo:
        client.chat_postMessage(channel=CHANNEL_ID, text="x")
    assert excinfo.value.response["message"] == "권한 없음"
    assert excinfo.value.response["ok"] is False


@respx.mock
def test_files_upload_posts_then_attaches(client: WebClient) -> None:
    upload = respx.post(f"{BASE}/files").mock(
        return_value=httpx.Response(
            201, json={"file_infos": [{"id": "file000000000000000000000", "name": "a.txt"}]}
        )
    )
    post = respx.post(f"{BASE}/posts").mock(return_value=httpx.Response(201, json=make_post()))
    response = client.files_upload_v2(
        channel=CHANNEL_ID, content="hello", filename="a.txt", initial_comment="첨부"
    )
    assert upload.called
    assert _json(post)["file_ids"] == ["file000000000000000000000"]
    assert response["ok"] is True


def _json(route: Any) -> dict[str, Any]:
    import json

    return json.loads(route.calls.last.request.content)
