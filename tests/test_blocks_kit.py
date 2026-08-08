"""Block Kit → Mattermost attachments / dialog 변환."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from mattermost_bolt.blocks.kit import (
    blocks_to_attachments,
    blocks_to_fallback_text,
    dialog_submission_to_view_state,
    view_to_dialog,
)

ACTION_URL = "http://app.test/mmbolt/actions"


def section(text: str, **extra: Any) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}, **extra}


def test_section_text_becomes_attachment_text() -> None:
    attachments = blocks_to_attachments([section("*hi*")], action_url=ACTION_URL)
    assert attachments == [{"text": "**hi**"}]


def test_header_and_divider() -> None:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "제목"}},
        {"type": "divider"},
        section("본문"),
    ]
    attachments = blocks_to_attachments(blocks, action_url=ACTION_URL)
    assert attachments[0]["text"] == "#### 제목\n\n---\n\n본문"


def test_section_fields_become_attachment_fields() -> None:
    blocks = [
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*A*"},
                {"type": "mrkdwn", "text": "*B*"},
            ],
        }
    ]
    fields = blocks_to_attachments(blocks, action_url=ACTION_URL)[0]["fields"]
    assert [f["value"] for f in fields] == ["**A**", "**B**"]
    assert all(f["short"] for f in fields)


def test_context_becomes_footer() -> None:
    blocks = [{"type": "context", "elements": [{"type": "mrkdwn", "text": "메타"}]}]
    assert blocks_to_attachments(blocks, action_url=ACTION_URL)[0]["footer"] == "메타"


def test_image_block() -> None:
    blocks = [{"type": "image", "image_url": "https://x.test/a.png", "alt_text": "a"}]
    assert blocks_to_attachments(blocks, action_url=ACTION_URL)[0]["image_url"] == (
        "https://x.test/a.png"
    )


def test_button_carries_action_id_in_integration_context() -> None:
    """왕복 식별자 보존이 인터랙티브 지원의 핵심이다."""
    blocks = [
        {
            "type": "actions",
            "block_id": "b1",
            "elements": [
                {
                    "type": "button",
                    "action_id": "approve",
                    "text": {"type": "plain_text", "text": "승인"},
                    "value": "42",
                    "style": "primary",
                }
            ],
        }
    ]
    action = blocks_to_attachments(blocks, action_url=ACTION_URL)[0]["actions"][0]
    assert action["id"] == "approve"
    assert action["name"] == "승인"
    assert action["style"] == "primary"
    assert action["integration"]["url"] == ACTION_URL
    assert action["integration"]["context"] == {
        "action_id": "approve",
        "block_id": "b1",
        "value": "42",
        "type": "button",
    }


def test_static_select_options() -> None:
    blocks = [
        {
            "type": "actions",
            "block_id": "b1",
            "elements": [
                {
                    "type": "static_select",
                    "action_id": "env",
                    "placeholder": {"type": "plain_text", "text": "환경"},
                    "options": [
                        {"text": {"type": "plain_text", "text": "운영"}, "value": "prod"},
                        {"text": {"type": "plain_text", "text": "개발"}, "value": "dev"},
                    ],
                }
            ],
        }
    ]
    action = blocks_to_attachments(blocks, action_url=ACTION_URL)[0]["actions"][0]
    assert action["type"] == "select"
    assert action["options"] == [
        {"text": "운영", "value": "prod"},
        {"text": "개발", "value": "dev"},
    ]


def test_users_select_uses_data_source() -> None:
    blocks = [
        {
            "type": "actions",
            "block_id": "b1",
            "elements": [{"type": "users_select", "action_id": "who"}],
        }
    ]
    action = blocks_to_attachments(blocks, action_url=ACTION_URL)[0]["actions"][0]
    assert action["data_source"] == "users"


def test_text_and_actions_keep_their_order() -> None:
    blocks = [
        section("첫 문단"),
        {
            "type": "actions",
            "block_id": "b1",
            "elements": [
                {"type": "button", "action_id": "a", "text": {"type": "plain_text", "text": "A"}}
            ],
        },
        section("둘째 문단"),
    ]
    attachments = blocks_to_attachments(blocks, action_url=ACTION_URL)
    assert len(attachments) == 2
    assert attachments[0]["text"] == "첫 문단"
    assert attachments[0]["actions"][0]["id"] == "a"
    assert attachments[1]["text"] == "둘째 문단"
    assert "actions" not in attachments[1]


def test_section_accessory_button_is_kept() -> None:
    blocks = [
        section(
            "본문",
            block_id="b1",
            accessory={
                "type": "button",
                "action_id": "go",
                "text": {"type": "plain_text", "text": "Go"},
            },
        )
    ]
    attachments = blocks_to_attachments(blocks, action_url=ACTION_URL)
    assert attachments[0]["actions"][0]["id"] == "go"


def test_unsupported_block_warns_and_does_not_vanish_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """조용히 사라지는 UI 가 마이그레이션에서 가장 찾기 어려운 결함이다."""
    logger = logging.getLogger("test.kit")
    with caplog.at_level(logging.WARNING, logger="test.kit"):
        blocks_to_attachments(
            [{"type": "rich_text", "elements": []}], action_url=ACTION_URL, logger=logger
        )
    assert any("rich_text" in record.getMessage() for record in caplog.records)


def test_unsupported_element_warns(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.kit2")
    blocks = [
        {
            "type": "actions",
            "block_id": "b1",
            "elements": [{"type": "datepicker", "action_id": "d"}],
        }
    ]
    with caplog.at_level(logging.WARNING, logger="test.kit2"):
        attachments = blocks_to_attachments(blocks, action_url=ACTION_URL, logger=logger)
    assert attachments == []
    assert caplog.records


def test_fallback_text_for_notifications() -> None:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "배포 완료"}},
        section("*prod* 반영됨"),
    ]
    assert blocks_to_fallback_text(blocks) == "배포 완료\n**prod** 반영됨"


def test_empty_blocks() -> None:
    assert blocks_to_attachments([], action_url=ACTION_URL) == []


# -- modal → dialog --------------------------------------------------------


def modal(*blocks: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "cb",
        "title": {"type": "plain_text", "text": "제목"},
        "submit": {"type": "plain_text", "text": "보내기"},
        "blocks": list(blocks),
        **extra,
    }


def input_block(action_id: str, block_id: str, **element: Any) -> dict[str, Any]:
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": block_id},
        "element": {"type": "plain_text_input", "action_id": action_id, **element},
    }


def test_modal_becomes_dialog() -> None:
    dialog, meta = view_to_dialog(modal(input_block("name", "b1")), action_url=ACTION_URL)
    assert dialog["callback_id"] == "cb"
    assert dialog["title"] == "제목"
    assert dialog["submit_label"] == "보내기"
    assert dialog["url"] == ACTION_URL
    assert dialog["elements"][0]["name"] == "e0"
    assert dialog["elements"][0]["type"] == "text"
    assert meta["map"]["e0"] == ["b1", "name", "plain_text_input"]


def test_multiline_input_becomes_textarea() -> None:
    dialog, _ = view_to_dialog(
        modal(input_block("note", "b1", multiline=True)), action_url=ACTION_URL
    )
    assert dialog["elements"][0]["type"] == "textarea"


def test_optional_and_hint_are_carried() -> None:
    block = input_block("name", "b1")
    block["optional"] = True
    block["hint"] = {"type": "plain_text", "text": "도움말"}
    dialog, _ = view_to_dialog(modal(block), action_url=ACTION_URL)
    assert dialog["elements"][0]["optional"] is True
    assert dialog["elements"][0]["help_text"] == "도움말"


def test_select_input_options() -> None:
    block = {
        "type": "input",
        "block_id": "b1",
        "label": {"type": "plain_text", "text": "환경"},
        "element": {
            "type": "static_select",
            "action_id": "env",
            "options": [{"text": {"type": "plain_text", "text": "운영"}, "value": "prod"}],
        },
    }
    dialog, _ = view_to_dialog(modal(block), action_url=ACTION_URL)
    element = dialog["elements"][0]
    assert element["type"] == "select"
    assert element["options"] == [{"text": "운영", "value": "prod"}]


def test_state_carries_private_metadata_and_mapping() -> None:
    dialog, _ = view_to_dialog(
        modal(input_block("name", "b1"), private_metadata="ctx-123"),
        action_url=ACTION_URL,
    )
    state = json.loads(dialog["state"])
    assert state["pm"] == "ctx-123"
    assert state["cb"] == "cb"


def test_section_blocks_become_introduction_text() -> None:
    dialog, _ = view_to_dialog(
        modal(section("설명문"), input_block("name", "b1")), action_url=ACTION_URL
    )
    assert dialog["introduction_text"] == "설명문"


def test_submission_values_are_typed_by_element() -> None:
    dialog, meta = view_to_dialog(
        modal(
            input_block("name", "b1"),
            {
                "type": "input",
                "block_id": "b2",
                "label": {"type": "plain_text", "text": "동의"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "agree",
                    "options": [{"text": {"type": "plain_text", "text": "예"}, "value": "y"}],
                },
            },
        ),
        action_url=ACTION_URL,
    )
    assert dialog["elements"][1]["type"] == "bool"

    values = dialog_submission_to_view_state({"e0": "홍길동", "e1": True}, meta)
    assert values["b1"]["name"]["value"] == "홍길동"
    assert values["b2"]["agree"]["selected_options"] == [{"value": "true"}]


def test_unknown_input_element_falls_back_to_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.kit3")
    block = {
        "type": "input",
        "block_id": "b1",
        "label": {"type": "plain_text", "text": "날짜"},
        "element": {"type": "datepicker", "action_id": "d"},
    }
    with caplog.at_level(logging.WARNING, logger="test.kit3"):
        dialog, _ = view_to_dialog(modal(block), action_url=ACTION_URL, logger=logger)
    assert dialog["elements"][0]["type"] == "text"
    assert caplog.records


def test_large_state_warns(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.kit4")
    view = modal(input_block("name", "b1"), private_metadata="x" * 5000)
    with caplog.at_level(logging.WARNING, logger="test.kit4"):
        view_to_dialog(view, action_url=ACTION_URL, logger=logger)
    assert any("state" in record.message for record in caplog.records)


def test_element_names_are_stable_across_blocks() -> None:
    """이름은 블록 인덱스 기반이라 순서가 같으면 동일하다."""
    view = modal(input_block("a", "b1"), input_block("b", "b2"))
    dialog1, _ = view_to_dialog(view, action_url=ACTION_URL)
    dialog2, _ = view_to_dialog(view, action_url=ACTION_URL)
    names: list[str] = [e["name"] for e in dialog1["elements"]]
    assert names == [e["name"] for e in dialog2["elements"]] == ["e0", "e1"]
