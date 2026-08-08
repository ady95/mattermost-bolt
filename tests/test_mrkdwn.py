"""Slack mrkdwn → Mattermost 마크다운 변환."""

from __future__ import annotations

import pytest

from mattermost_bolt.blocks.mrkdwn import to_mattermost, to_slack


@pytest.mark.parametrize(
    ("slack", "expected"),
    [
        # 가장 중요한 차이 — Slack 의 *bold* 를 그대로 두면 Mattermost 에서 기울임이 된다.
        ("*bold*", "**bold**"),
        ("a *bold* b", "a **bold** b"),
        ("~strike~", "~~strike~~"),
        ("_italic_", "_italic_"),
        ("<https://x.test|링크>", "[링크](https://x.test)"),
        ("<https://x.test>", "https://x.test"),
        ("<@U123|carol>", "@carol"),
        ("<@U123>", "@U123"),
        ("<#C1|dev>", "~dev"),
        ("<!here>", "@here"),
        ("<!channel>", "@channel"),
        ("<!everyone>", "@all"),
        # 이미 Mattermost 문법이면 건드리지 않는다(이중 변환 방지).
        ("**already**", "**already**"),
        ("~~already~~", "~~already~~"),
        # 곱셈 기호나 목록 표시는 강조가 아니다.
        ("2 * 3 * 4", "2 * 3 * 4"),
        ("", ""),
    ],
)
def test_to_mattermost(slack: str, expected: str) -> None:
    assert to_mattermost(slack) == expected


def test_code_spans_are_untouched() -> None:
    """코드 안의 별표는 강조가 아니다."""
    assert to_mattermost("`*not bold*`") == "`*not bold*`"
    assert to_mattermost("```\n*x* <@U1>\n```") == "```\n*x* <@U1>\n```"


def test_mixed_code_and_text() -> None:
    source = "*bold* then `*code*` then *bold2*"
    assert to_mattermost(source) == "**bold** then `*code*` then **bold2**"


def test_multiline_preserved() -> None:
    source = "line1 *a*\nline2 *b*"
    assert to_mattermost(source) == "line1 **a**\nline2 **b**"


def test_round_trip_back_to_slack() -> None:
    assert to_slack("**bold**") == "*bold*"
    assert to_slack("~~s~~") == "~s~"
    assert to_slack("[t](https://x.test)") == "<https://x.test|t>"
