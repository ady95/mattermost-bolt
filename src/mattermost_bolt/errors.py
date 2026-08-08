"""예외 계층.

Slack SDK 의 ``SlackApiError`` / ``BoltError`` 에 대응한다.
기존 Bolt 앱이 ``except SlackApiError as e: e.response["error"]`` 형태로
오류를 다루므로 같은 접근 방식을 유지한다.
"""

from __future__ import annotations

from typing import Any


class BoltError(Exception):
    """설정 오류 등 프레임워크 자체 오류."""


class MattermostApiError(BoltError):
    """Mattermost REST API 가 2xx 이외를 반환했을 때 발생한다.

    ``SlackApiError`` 와 동일하게 ``.response`` 로 본문에 접근한다.
    """

    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response

    def __str__(self) -> str:
        base = super().__str__()
        detail = None
        if self.response is not None:
            try:
                detail = self.response.get("error") or self.response.get("message")
            except Exception:
                detail = None
        return f"{base} ({detail})" if detail else base


class UnsupportedFeatureError(BoltError):
    """Mattermost 에 대응 개념이 없는 Slack 기능을 호출했을 때 발생한다.

    조용히 무시하지 않고 대안을 메시지에 담아 알린다 (실행계획서 §2.5).
    """


# Slack SDK 호환 별칭 — ``except SlackApiError`` 코드를 그대로 통과시킨다.
SlackApiError = MattermostApiError
