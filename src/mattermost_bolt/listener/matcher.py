"""리스너 매처.

Slack Bolt 와 같은 인자 형태를 받는다: 문자열, 컴파일된 정규식, dict 제약.
정규식 캡처 그룹은 ``context["matches"]`` 로 넘겨 ``@app.message`` 사용 관례를
유지한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from re import Pattern
from typing import Any, Callable, Union

from ..request import (
    KIND_ACTION,
    KIND_COMMAND,
    KIND_EVENT,
    KIND_VIEW,
    BoltRequest,
)

Matcher = Callable[[BoltRequest], bool]
PatternLike = Union[str, Pattern[str]]


def _compile(pattern: PatternLike) -> Pattern[str]:
    if isinstance(pattern, re.Pattern):
        return pattern
    return re.compile(pattern)


def _search(pattern: PatternLike, text: str, request: BoltRequest) -> bool:
    """정규식 검색 후 캡처 그룹을 context 에 싣는다."""
    match = _compile(pattern).search(text or "")
    if match is None:
        return False
    groups = list(match.groups())
    request.context["matches"] = groups if groups else [match.group(0)]
    return True


def message_matcher(pattern: PatternLike | None, *, ignore_subtypes: Sequence[str] = ()) -> Matcher:
    """``@app.message(...)`` — 메시지 본문 검색.

    Slack Bolt 와 동일하게 문자열도 정규식으로 해석한다
    (``app.message("hello")`` 는 "hello" 를 포함한 메시지에 반응한다).
    """
    skip = set(ignore_subtypes)

    def matcher(request: BoltRequest) -> bool:
        if request.kind != KIND_EVENT:
            return False
        event = request.body.get("event", {})
        if event.get("type") != "message":
            return False
        subtype = event.get("subtype")
        if subtype in skip:
            return False
        if pattern is None:
            return True
        return _search(pattern, event.get("text", ""), request)

    return matcher


def event_matcher(constraint: str | dict[str, Any]) -> Matcher:
    """``@app.event(...)`` — 이벤트 타입/서브타입 일치."""
    if isinstance(constraint, dict):
        wanted_type = constraint.get("type")
        wanted_subtype = constraint.get("subtype", ...)
    else:
        wanted_type = constraint
        wanted_subtype = ...

    def matcher(request: BoltRequest) -> bool:
        if request.kind != KIND_EVENT:
            return False
        event = request.body.get("event", {})
        if event.get("type") != wanted_type:
            return False
        if wanted_subtype is ...:
            return True
        return event.get("subtype") == wanted_subtype

    return matcher


def command_matcher(pattern: PatternLike) -> Matcher:
    """``@app.command(...)`` — 명령어 일치. 앞의 ``/`` 유무를 흡수한다."""

    def matcher(request: BoltRequest) -> bool:
        if request.kind != KIND_COMMAND:
            return False
        actual = request.body.get("command", "")
        if isinstance(pattern, str):
            return actual.lstrip("/") == pattern.lstrip("/")
        return _search(pattern, actual, request)

    return matcher


def action_matcher(constraint: str | Pattern[str] | dict[str, Any]) -> Matcher:
    """``@app.action(...)`` — ``action_id`` 또는 ``block_id`` 일치."""

    def matcher(request: BoltRequest) -> bool:
        if request.kind != KIND_ACTION:
            return False
        action = request.payload
        if isinstance(constraint, dict):
            for key, expected in constraint.items():
                actual = action.get(key)
                if isinstance(expected, re.Pattern):
                    if not expected.search(actual or ""):
                        return False
                elif actual != expected:
                    return False
            return True
        if isinstance(constraint, str):
            return action.get("action_id") == constraint
        return _search(constraint, action.get("action_id", ""), request)

    return matcher


def view_matcher(
    constraint: str | Pattern[str] | dict[str, Any], *, closed: bool = False
) -> Matcher:
    """``@app.view(...)`` — ``callback_id`` 일치."""
    wanted_type = "view_closed" if closed else "view_submission"

    def matcher(request: BoltRequest) -> bool:
        if request.kind != KIND_VIEW:
            return False
        if request.body.get("type") != wanted_type:
            return False
        callback_id = request.body.get("view", {}).get("callback_id", "")
        if isinstance(constraint, dict):
            expected = constraint.get("callback_id")
            return callback_id == expected
        if isinstance(constraint, str):
            return callback_id == constraint
        return _search(constraint, callback_id, request)

    return matcher


def all_of(*matchers: Matcher) -> Matcher:
    """모든 매처를 만족해야 통과."""

    def matcher(request: BoltRequest) -> bool:
        return all(m(request) for m in matchers)

    return matcher
