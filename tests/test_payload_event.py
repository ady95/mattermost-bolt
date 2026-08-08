"""WebSocket 이벤트 정규화 — 실측 프레임 기준."""

from __future__ import annotations

import fixtures
from mattermost_bolt.payload.event import normalize_ws_event, parse_post
from mattermost_bolt.ts import TsCodec


def test_posted_becomes_slack_message_event() -> None:
    body = normalize_ws_event(fixtures.ws_posted(), TsCodec())
    assert body is not None
    assert body["type"] == "event_callback"
    assert body["team_id"] == fixtures.TEAM_ID

    event = body["event"]
    assert event["type"] == "message"
    assert event["channel"] == fixtures.CHANNEL_ID
    assert event["user"] == fixtures.USER_ID
    assert event["text"] == "spike message *bold* 한글"
    assert event["ts"] == fixtures.POST_ID
    assert event["channel_type"] == "channel"
    assert "subtype" not in event


def test_event_time_is_epoch_seconds_not_post_id() -> None:
    """post_id 모드에서 event_ts 는 숫자가 아니다.

    이 케이스가 스파이크에서 실제로 터졌다(int('kb17n...') → ValueError).
    """
    body = normalize_ws_event(fixtures.ws_posted(), TsCodec())
    assert body is not None
    assert body["event_time"] == fixtures.CREATE_AT // 1000
    assert isinstance(body["event_time"], int)


def test_post_json_string_is_parsed() -> None:
    """``data.post`` 는 dict 가 아니라 JSON 문자열로 온다."""
    data = fixtures.ws_posted()["data"]
    assert isinstance(data["post"], str)
    assert parse_post(data)["id"] == fixtures.POST_ID


def test_parse_post_survives_garbage() -> None:
    assert parse_post({"post": "{not json"}) == {}
    assert parse_post({}) == {}


def test_thread_reply_maps_root_id_to_thread_ts() -> None:
    frame = fixtures.ws_posted(root_id=fixtures.ROOT_POST_ID, id="replypost0000000000000000")
    body = normalize_ws_event(frame, TsCodec())
    assert body is not None
    assert body["event"]["thread_ts"] == fixtures.ROOT_POST_ID
    assert body["event"]["ts"] == "replypost0000000000000000"


def test_bot_message_gets_bot_id_and_subtype() -> None:
    frame = fixtures.ws_posted(props={"from_bot": "true", "override_username": "deploybot"})
    body = normalize_ws_event(frame, TsCodec())
    assert body is not None
    event = body["event"]
    assert event["subtype"] == "bot_message"
    assert event["bot_id"] == "deploybot"
    assert event["username"] == "deploybot"


def test_system_message_maps_to_subtype() -> None:
    frame = fixtures.ws_posted(type="system_join_channel")
    body = normalize_ws_event(frame, TsCodec())
    assert body is not None
    assert body["event"]["subtype"] == "channel_join"


def test_post_edited_marks_message_changed() -> None:
    body = normalize_ws_event(fixtures.ws_post_edited(), TsCodec())
    assert body is not None
    assert body["event"]["subtype"] == "message_changed"
    assert body["event"]["text"] == "spike message edited"
    # data 에 channel_name/channel_type 이 없어도 broadcast 로 채널을 찾는다.
    assert body["event"]["channel"] == fixtures.CHANNEL_ID


def test_post_deleted_marks_message_deleted() -> None:
    body = normalize_ws_event(fixtures.ws_post_deleted(), TsCodec())
    assert body is not None
    assert body["event"]["subtype"] == "message_deleted"


def test_reaction_added_uses_broadcast_channel() -> None:
    """reaction 프레임의 ``data`` 에는 channel_id 가 없다."""
    frame = fixtures.ws_reaction_added()
    assert "channel_id" not in frame["data"]

    body = normalize_ws_event(frame, TsCodec())
    assert body is not None
    event = body["event"]
    assert event["type"] == "reaction_added"
    assert event["channel"] == fixtures.CHANNEL_ID
    assert event["reaction"] == "+1"
    assert event["user"] == fixtures.USER_ID
    assert event["item"]["ts"] == fixtures.POST_ID


def test_unknown_event_returns_none() -> None:
    assert normalize_ws_event({"event": "sidebar_category_updated", "data": {}}, TsCodec()) is None
    assert normalize_ws_event({}, TsCodec()) is None


def test_epoch_ts_mode_produces_slack_shaped_ts() -> None:
    codec = TsCodec("epoch")
    body = normalize_ws_event(fixtures.ws_posted(), codec)
    assert body is not None
    ts = body["event"]["ts"]
    assert ts == "1786160418.395000"
    assert float(ts) > 0  # 산술 연산을 하는 앱을 위한 모드다
    assert codec.decode(ts) == fixtures.POST_ID


def test_files_are_exposed() -> None:
    frame = fixtures.ws_posted(
        metadata={"files": [{"id": "f1", "name": "a.png", "mime_type": "image/png", "size": 10}]}
    )
    body = normalize_ws_event(frame, TsCodec())
    assert body is not None
    assert body["event"]["files"][0]["name"] == "a.png"


def test_original_post_is_preserved_for_escape_hatch() -> None:
    body = normalize_ws_event(fixtures.ws_posted(), TsCodec())
    assert body is not None
    assert body["event"]["mattermost_post"]["id"] == fixtures.POST_ID
