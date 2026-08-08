"""command / action / dialog 페이로드 정규화."""

from __future__ import annotations

import json

import fixtures
from mattermost_bolt.blocks.kit import view_to_dialog
from mattermost_bolt.payload.action import (
    errors_to_dialog_response,
    normalize_action,
    normalize_dialog,
)
from mattermost_bolt.payload.command import (
    build_command_from_message,
    normalize_command,
    split_args,
)
from mattermost_bolt.ts import TsCodec

# -- slash command ---------------------------------------------------------


def test_command_fields_pass_through_unchanged() -> None:
    """Mattermost 와 Slack 의 command 필드는 사실상 동일하다."""
    payload = normalize_command(fixtures.http_command_form())
    form = fixtures.http_command_form()
    for key in ("channel_id", "user_id", "team_id", "text", "response_url", "trigger_id"):
        assert payload[key] == form[key]
    assert payload["command"] == "/boltspike"


def test_command_gets_slack_only_fields() -> None:
    payload = normalize_command(fixtures.http_command_form())
    assert payload["api_app_id"] == "mattermost-bolt"
    assert payload["enterprise_id"] is None
    assert payload["is_enterprise_install"] is False


def test_command_leading_slash_is_normalized() -> None:
    form = fixtures.http_command_form()
    form["command"] = "boltspike"
    assert normalize_command(form)["command"] == "/boltspike"


def test_pseudo_socket_mode_builds_command_from_text() -> None:
    event = {"channel": "C1", "user": "U1", "text": "/deploy prod --force"}
    payload = build_command_from_message("/deploy prod --force", event=event)
    assert payload is not None
    assert payload["command"] == "/deploy"
    assert payload["text"] == "prod --force"
    assert payload["mattermost_source"] == "websocket"
    # WebSocket 경로에는 이 두 가지가 없다 — 다이얼로그를 열 수 없는 이유다.
    assert payload["trigger_id"] == ""
    assert payload["response_url"] == ""


def test_plain_message_is_not_a_command() -> None:
    assert build_command_from_message("hello", event={}) is None
    assert build_command_from_message("", event={}) is None


def test_split_args_honours_quotes() -> None:
    assert split_args('deploy "my app" --force') == ("deploy", "my app", "--force")
    assert split_args('unbalanced "quote') == ("unbalanced", '"quote')


# -- interactive action ----------------------------------------------------


def test_action_becomes_block_actions() -> None:
    body = normalize_action(fixtures.http_action_body(), TsCodec())
    assert body["type"] == "block_actions"
    assert body["user"]["id"] == fixtures.USER_ID
    assert body["channel"]["id"] == fixtures.CHANNEL_ID
    assert body["trigger_id"]

    action = body["actions"][0]
    assert action["action_id"] == "spike_button"
    assert action["block_id"] == "spike_block"
    assert action["value"] == "spike-value"
    assert action["type"] == "button"


def test_select_action_exposes_selected_option() -> None:
    raw = fixtures.http_action_body(type="static_select", selected_option="b")
    body = normalize_action(raw, TsCodec())
    action = body["actions"][0]
    assert action["type"] == "static_select"
    assert action["selected_option"]["value"] == "b"
    assert action["value"] == "b"


def test_action_message_ts_is_the_post_id() -> None:
    body = normalize_action(fixtures.http_action_body(), TsCodec())
    assert body["container"]["message_ts"] == fixtures.POST_ID


# -- dialog ----------------------------------------------------------------


def _dialog_state() -> str:
    view = {
        "type": "modal",
        "callback_id": "spike_dialog",
        "title": {"type": "plain_text", "text": "T"},
        "private_metadata": "carry-me",
        "blocks": [
            {
                "type": "input",
                "block_id": "name_block",
                "label": {"type": "plain_text", "text": "이름"},
                "element": {"type": "plain_text_input", "action_id": "name_input"},
            }
        ],
    }
    dialog, _ = view_to_dialog(view, action_url="http://app.test/mmbolt/dialogs")
    return dialog["state"]


def test_dialog_submission_restores_slack_view_state() -> None:
    """Mattermost element name(e0) → Slack block_id/action_id 복원."""
    raw = fixtures.http_dialog_body(state=_dialog_state(), submission={"e0": "홍길동"})
    body = normalize_dialog(raw)
    assert body["type"] == "view_submission"
    values = body["view"]["state"]["values"]
    assert values["name_block"]["name_input"]["value"] == "홍길동"


def test_private_metadata_survives_the_round_trip() -> None:
    raw = fixtures.http_dialog_body(state=_dialog_state())
    body = normalize_dialog(raw)
    assert body["view"]["private_metadata"] == "carry-me"


def test_cancelled_dialog_becomes_view_closed() -> None:
    raw = fixtures.http_dialog_body(state=_dialog_state(), cancelled=True)
    body = normalize_dialog(raw)
    assert body["type"] == "view_closed"
    assert body["is_cleared"] is True


def test_hand_written_state_is_treated_as_private_metadata() -> None:
    """mattermost-bolt 가 만들지 않은 다이얼로그도 처리할 수 있어야 한다."""
    raw = fixtures.http_dialog_body(state="opaque-string", submission={"foo": "bar"})
    body = normalize_dialog(raw)
    assert body["view"]["private_metadata"] == "opaque-string"
    assert body["view"]["state"]["values"]["foo"]["foo"]["value"] == "bar"


def test_errors_map_block_id_back_to_element_name() -> None:
    state_meta = json.loads(_dialog_state())
    response = errors_to_dialog_response({"name_block": "필수입니다"}, state_meta)
    assert response == {"errors": {"e0": "필수입니다"}}
