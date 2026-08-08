"""Block Kit → Mattermost 변환기 (실행계획서 결정 D5).

Mattermost 에는 Block Kit 이 없다. 대신 두 가지 대응물이 있다.

- 메시지 UI  : Slack 호환 **Message Attachments**
- 모달 UI    : **Interactive Dialog**

전부를 옮길 수는 없으므로 이 변환기는 **손실 허용(lossy)** 으로 동작한다.
대응물이 없는 블록은 텍스트로 폴백하고 ``logger.warning`` 을 남긴다.
조용히 사라지는 UI 요소가 마이그레이션에서 가장 찾기 어려운 버그이므로,
무시할 때는 반드시 흔적을 남긴다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from .mrkdwn import to_mattermost

_default_logger = logging.getLogger("mattermost_bolt.blocks")

# dialog 의 state 에 얹는 메타데이터 크기 상한(경고 기준).
_STATE_WARN_SIZE = 4000

# Block Kit 의 style → Mattermost attachment action style.
_STYLE_MAP = {"primary": "primary", "danger": "danger", "default": "default"}


def plain_text(node: Any, *, convert_mrkdwn: bool = True) -> str:
    """``{"type": "plain_text"|"mrkdwn", "text": ...}`` 또는 문자열에서 텍스트 추출."""
    if node is None:
        return ""
    if isinstance(node, str):
        return to_mattermost(node) if convert_mrkdwn else node
    if isinstance(node, dict):
        text = node.get("text", "")
        if isinstance(text, dict):  # 중첩된 text 객체
            text = text.get("text", "")
        if node.get("type") == "mrkdwn" and convert_mrkdwn:
            return to_mattermost(text)
        return text
    return str(node)


# ---------------------------------------------------------------------------
# 메시지: blocks → attachments
# ---------------------------------------------------------------------------


def _button_to_action(
    element: dict[str, Any],
    block_id: str,
    action_url: str,
) -> dict[str, Any]:
    action_id = element.get("action_id") or block_id
    action: dict[str, Any] = {
        "id": action_id,
        "name": plain_text(element.get("text")),
        "type": "button",
        "integration": {
            "url": action_url,
            "context": {
                "action_id": action_id,
                "block_id": block_id,
                "value": element.get("value"),
                "type": "button",
            },
        },
    }
    style = _STYLE_MAP.get(element.get("style", ""))
    if style and style != "default":
        action["style"] = style
    return action


def _select_to_action(
    element: dict[str, Any],
    block_id: str,
    action_url: str,
) -> dict[str, Any]:
    action_id = element.get("action_id") or block_id
    etype = element.get("type", "")
    action: dict[str, Any] = {
        "id": action_id,
        "name": plain_text(element.get("placeholder")) or "Select",
        "type": "select",
        "integration": {
            "url": action_url,
            "context": {
                "action_id": action_id,
                "block_id": block_id,
                "type": etype,
            },
        },
    }
    if etype in ("users_select", "multi_users_select"):
        action["data_source"] = "users"
    elif etype in ("channels_select", "conversations_select"):
        action["data_source"] = "channels"
    else:
        action["options"] = [
            {"text": plain_text(o.get("text")), "value": o.get("value", "")}
            for o in element.get("options", [])
        ]
    return action


def _element_to_action(
    element: dict[str, Any],
    block_id: str,
    action_url: str,
    logger: logging.Logger,
) -> dict[str, Any] | None:
    etype = element.get("type")
    if etype == "button":
        return _button_to_action(element, block_id, action_url)
    if etype in (
        "static_select",
        "external_select",
        "users_select",
        "channels_select",
        "conversations_select",
        "multi_static_select",
        "multi_users_select",
    ):
        return _select_to_action(element, block_id, action_url)
    logger.warning(
        "Block Kit element type %r 은 Mattermost 에 대응물이 없어 생략합니다 "
        "(block_id=%s). 버튼 또는 셀렉트로 대체하세요.",
        etype,
        block_id,
    )
    return None


def blocks_to_attachments(
    blocks: Sequence[dict[str, Any]],
    *,
    action_url: str = "",
    convert_mrkdwn: bool = True,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """Block Kit 블록 목록을 Mattermost attachment 목록으로 변환한다.

    액션이 등장할 때마다 attachment 를 끊어, 텍스트와 버튼의 순서를
    가능한 한 원본에 가깝게 유지한다.
    """
    log = logger or _default_logger
    attachments: list[dict[str, Any]] = []

    text_parts: list[str] = []
    fields: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    image_url: str | None = None
    footer: str | None = None

    def flush(force: bool = False) -> None:
        nonlocal text_parts, fields, actions, image_url, footer
        if not (text_parts or fields or actions or image_url or footer) and not force:
            return
        att: dict[str, Any] = {}
        if text_parts:
            att["text"] = "\n\n".join(p for p in text_parts if p)
        if fields:
            att["fields"] = fields
        if actions:
            att["actions"] = actions
        if image_url:
            att["image_url"] = image_url
        if footer:
            att["footer"] = footer
        if att:
            attachments.append(att)
        text_parts, fields, actions = [], [], []
        image_url, footer = None, None

    def txt(node: Any) -> str:
        return plain_text(node, convert_mrkdwn=convert_mrkdwn)

    for block in blocks or []:
        btype = block.get("type")
        block_id = block.get("block_id") or btype or ""

        if btype == "section":
            if block.get("text"):
                text_parts.append(txt(block["text"]))
            for f in block.get("fields", []) or []:
                fields.append({"title": "", "value": txt(f), "short": True})
            accessory = block.get("accessory")
            if accessory:
                action = _element_to_action(accessory, block_id, action_url, log)
                if action:
                    actions.append(action)
                    flush()
        elif btype == "header":
            text_parts.append(f"#### {txt(block.get('text'))}")
        elif btype == "divider":
            text_parts.append("---")
        elif btype == "context":
            pieces = [txt(e) for e in block.get("elements", []) if e.get("type") != "image"]
            joined = "  ".join(p for p in pieces if p)
            if joined:
                footer = joined if footer is None else f"{footer}  {joined}"
        elif btype == "image":
            if block.get("title"):
                text_parts.append(txt(block["title"]))
            image_url = block.get("image_url")
        elif btype == "actions":
            for element in block.get("elements", []) or []:
                action = _element_to_action(element, block_id, action_url, log)
                if action:
                    actions.append(action)
            flush()
        elif btype == "input":
            # 메시지에는 입력 블록을 놓을 수 없다. 모달에서만 유효하다.
            log.warning(
                "input 블록은 메시지에 렌더할 수 없습니다 (block_id=%s). "
                "client.views_open() 으로 다이얼로그를 여세요.",
                block_id,
            )
        else:
            log.warning(
                "지원하지 않는 블록 타입 %r 을 텍스트로 폴백합니다 (block_id=%s).",
                btype,
                block_id,
            )
            fallback = txt(block.get("text")) if block.get("text") else ""
            if fallback:
                text_parts.append(fallback)

    flush()
    return attachments


def blocks_to_fallback_text(
    blocks: Sequence[dict[str, Any]], *, convert_mrkdwn: bool = True
) -> str:
    """알림·검색용 평문 폴백 텍스트를 만든다."""
    parts: list[str] = []
    for block in blocks or []:
        if block.get("type") in ("section", "header") and block.get("text"):
            parts.append(plain_text(block["text"], convert_mrkdwn=convert_mrkdwn))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 모달: view → dialog
# ---------------------------------------------------------------------------

# Slack input element type → (MM element type, subtype)
_INPUT_MAP = {
    "plain_text_input": ("text", ""),
    "email_text_input": ("text", "email"),
    "url_text_input": ("text", "url"),
    "number_input": ("text", "number"),
    "static_select": ("select", ""),
    "external_select": ("select", ""),
    "users_select": ("select", ""),
    "channels_select": ("select", ""),
    "conversations_select": ("select", ""),
    "radio_buttons": ("radio", ""),
    "checkboxes": ("bool", ""),
}


def view_to_dialog(
    view: dict[str, Any],
    *,
    action_url: str,
    convert_mrkdwn: bool = True,
    logger: logging.Logger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Slack modal view 를 Mattermost dialog 로 변환한다.

    Returns:
        ``(dialog, state_meta)`` — ``state_meta`` 는 제출 시 Slack ``view`` 를
        복원하기 위한 매핑 정보이며 dialog 의 ``state`` 에 JSON 으로 실린다.

    Mattermost dialog element 의 ``name`` 에는 문자 제약이 있어
    ``block_id``/``action_id`` 를 직접 넣지 않는다. 대신 ``e0``, ``e1`` … 로
    발급하고 원래 식별자는 ``state`` 에 담아 되돌린다.
    """
    log = logger or _default_logger
    elements: list[dict[str, Any]] = []
    mapping: dict[str, list[str]] = {}

    def txt(node: Any) -> str:
        return plain_text(node, convert_mrkdwn=convert_mrkdwn)

    for index, block in enumerate(view.get("blocks", []) or []):
        if block.get("type") != "input":
            if block.get("type") in ("section", "header") and block.get("text"):
                # 안내 문구는 introduction_text 로 합류시킨다.
                mapping.setdefault("_intro", []).append(txt(block["text"]))
            elif block.get("type") == "divider":
                pass
            else:
                log.warning(
                    "다이얼로그에서 지원하지 않는 블록 %r 을 생략합니다.",
                    block.get("type"),
                )
            continue

        element = block.get("element", {}) or {}
        etype = element.get("type", "")
        mapped = _INPUT_MAP.get(etype)
        if mapped is None:
            log.warning("input element %r 은 대응물이 없어 텍스트 입력으로 폴백합니다.", etype)
            mapped = ("text", "")

        mm_type, subtype = mapped
        name = f"e{index}"
        block_id = block.get("block_id") or f"block_{index}"
        action_id = element.get("action_id") or f"action_{index}"
        mapping[name] = [block_id, action_id, etype]

        el: dict[str, Any] = {
            "display_name": txt(block.get("label")) or name,
            "name": name,
            "type": mm_type,
            "optional": bool(block.get("optional", False)),
        }
        if subtype:
            el["subtype"] = subtype
        if element.get("multiline"):
            el["type"] = "textarea"
        if block.get("hint"):
            el["help_text"] = txt(block["hint"])
        if element.get("placeholder"):
            el["placeholder"] = txt(element["placeholder"])
        if element.get("initial_value") is not None:
            el["default"] = str(element["initial_value"])

        if mm_type == "select":
            if etype in ("users_select", "conversations_select"):
                el["data_source"] = "users"
            elif etype == "channels_select":
                el["data_source"] = "channels"
            else:
                el["options"] = [
                    {"text": txt(o.get("text")), "value": o.get("value", "")}
                    for o in element.get("options", []) or []
                ]
        elif mm_type == "radio":
            el["options"] = [
                {"text": txt(o.get("text")), "value": o.get("value", "")}
                for o in element.get("options", []) or []
            ]
        elif mm_type == "bool":
            opts = element.get("options") or []
            el["placeholder"] = txt(opts[0].get("text")) if opts else el["display_name"]

        elements.append(el)

    intro_parts = mapping.pop("_intro", [])
    state_meta = {
        "pm": view.get("private_metadata", ""),
        "cb": view.get("callback_id", ""),
        "map": mapping,
    }
    state = json.dumps(state_meta, ensure_ascii=False)
    if len(state) > _STATE_WARN_SIZE:
        log.warning(
            "dialog state 가 %d 바이트로 큽니다. private_metadata 를 줄이세요.",
            len(state),
        )

    dialog: dict[str, Any] = {
        "callback_id": view.get("callback_id", ""),
        "title": txt(view.get("title")) or "Dialog",
        "elements": elements,
        "state": state,
        "notify_on_cancel": True,
        "url": action_url,
    }
    if intro_parts:
        dialog["introduction_text"] = "\n\n".join(intro_parts)
    if view.get("submit"):
        dialog["submit_label"] = txt(view["submit"])

    return dialog, state_meta


def dialog_submission_to_view_state(
    submission: dict[str, Any], state_meta: dict[str, Any]
) -> dict[str, Any]:
    """dialog 제출값을 Slack ``view.state.values`` 구조로 되돌린다."""
    mapping: dict[str, Any] = state_meta.get("map", {}) or {}
    values: dict[str, dict[str, Any]] = {}
    for name, raw in (submission or {}).items():
        entry = mapping.get(name)
        if entry:
            block_id, action_id, etype = ([*entry, "", "", ""])[:3]
        else:
            # 매핑에 없는 값(수동 dialog 등)은 name 을 그대로 식별자로 쓴다.
            block_id, action_id, etype = name, name, "plain_text_input"
        values.setdefault(block_id, {})[action_id] = _wrap_value(etype, raw)
    return values


def _wrap_value(etype: str, raw: Any) -> dict[str, Any]:
    """Slack view state 의 값 표현으로 감싼다."""
    if etype in ("static_select", "external_select"):
        return {
            "type": etype,
            "selected_option": (
                {"value": raw, "text": {"type": "plain_text", "text": str(raw)}}
                if raw not in (None, "")
                else None
            ),
        }
    if etype == "users_select":
        return {"type": etype, "selected_user": raw}
    if etype in ("channels_select", "conversations_select"):
        return {"type": etype, "selected_channel": raw}
    if etype == "radio_buttons":
        return {
            "type": etype,
            "selected_option": (
                {"value": raw, "text": {"type": "plain_text", "text": str(raw)}}
                if raw not in (None, "")
                else None
            ),
        }
    if etype == "checkboxes":
        selected = [{"value": "true"}] if raw in (True, "true", "True") else []
        return {"type": etype, "selected_options": selected}
    return {"type": etype or "plain_text_input", "value": raw}
