r"""Slack mrkdwn → Mattermost 마크다운 변환.

두 포맷은 비슷해 보이지만 강조 문법이 다르다. Slack 의 ``*bold*`` 를 그대로
Mattermost 에 보내면 *기울임* 으로 렌더된다. 자동 변환하지 않으면 마이그레이션한
앱의 모든 메시지 서식이 조용히 어긋난다.

| Slack            | Mattermost        |
|------------------|-------------------|
| ``*bold*``       | ``**bold**``      |
| ``_italic_``     | ``_italic_`` (동일) |
| ``~strike~``     | ``~~strike~~``    |
| ``<url\|label>`` | ``[label](url)``  |
| ``<@U123>``      | ``@U123``         |
| ``<#C1\|dev>``   | ``~dev``          |
| ``<!here>``      | ``@here``         |

코드 블록/인라인 코드 안은 변환하지 않는다.
"""

from __future__ import annotations

import re

# 코드 영역(```fence``` 또는 `inline`)을 먼저 떼어내기 위한 패턴.
_CODE_SPLIT = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)

_LINK_LABELED = re.compile(r"<((?:https?|mailto|tel|ftp)[^|>]*)\|([^>]*)>")
_LINK_BARE = re.compile(r"<((?:https?|mailto|tel|ftp)[^|>]*)>")
_USER_LABELED = re.compile(r"<@([A-Za-z0-9_.\-]+)\|([^>]*)>")
_USER_BARE = re.compile(r"<@([A-Za-z0-9_.\-]+)>")
_CHANNEL_LABELED = re.compile(r"<#([A-Za-z0-9_.\-]+)\|([^>]*)>")
_CHANNEL_BARE = re.compile(r"<#([A-Za-z0-9_.\-]+)>")
_SPECIAL = re.compile(r"<!(here|channel|everyone)(\|[^>]*)?>")

# ``*bold*`` — 앞뒤가 별표/단어문자가 아니고, 내부에 개행·별표가 없을 때만.
_BOLD = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
# ``~strike~`` — 이미 ``~~`` 인 경우는 건드리지 않는다.
_STRIKE = re.compile(r"(?<![~\w])~(?!\s)([^~\n]+?)(?<!\s)~(?![~\w])")

_SPECIAL_MAP = {"here": "@here", "channel": "@channel", "everyone": "@all"}


def _convert_segment(text: str) -> str:
    text = _LINK_LABELED.sub(lambda m: f"[{m.group(2)}]({m.group(1)})", text)
    text = _LINK_BARE.sub(lambda m: m.group(1), text)
    text = _USER_LABELED.sub(lambda m: f"@{m.group(2)}", text)
    text = _USER_BARE.sub(lambda m: f"@{m.group(1)}", text)
    text = _CHANNEL_LABELED.sub(lambda m: f"~{m.group(2)}", text)
    text = _CHANNEL_BARE.sub(lambda m: f"~{m.group(1)}", text)
    text = _SPECIAL.sub(lambda m: _SPECIAL_MAP[m.group(1)], text)
    text = _BOLD.sub(lambda m: f"**{m.group(1)}**", text)
    text = _STRIKE.sub(lambda m: f"~~{m.group(1)}~~", text)
    return text


def to_mattermost(text: str) -> str:
    """Slack mrkdwn 문자열을 Mattermost 마크다운으로 변환한다."""
    if not text:
        return text
    parts = _CODE_SPLIT.split(text)
    # split 결과에서 홀수 인덱스가 코드 영역이다.
    return "".join(part if i % 2 else _convert_segment(part) for i, part in enumerate(parts))


def to_slack(text: str) -> str:
    """Mattermost 마크다운을 Slack mrkdwn 으로 되돌린다 (강조 문법만)."""
    if not text:
        return text
    parts = _CODE_SPLIT.split(text)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part)
            continue
        part = re.sub(r"\*\*([^*\n]+?)\*\*", r"*\1*", part)
        part = re.sub(r"~~([^~\n]+?)~~", r"~\1~", part)
        part = re.sub(r"\[([^\]]*)\]\((https?://[^)]+)\)", r"<\2|\1>", part)
        out.append(part)
    return "".join(out)


def split_code_segments(text: str) -> list[tuple[bool, str]]:
    """``(is_code, segment)`` 목록으로 분해한다 (테스트·디버깅용)."""
    parts = _CODE_SPLIT.split(text)
    return [(bool(i % 2), part) for i, part in enumerate(parts)]
