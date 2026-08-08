"""``BoltContext`` — 리스너 사이에서 공유되는 요청 스코프 상태.

Slack Bolt 와 동일하게 dict 를 상속하고, 자주 쓰는 키는 프로퍼티로도 노출한다.
미들웨어가 ``context["foo"] = ...`` 로 값을 얹는 패턴을 그대로 지원한다.
"""

from __future__ import annotations

from typing import Any


class BoltContext(dict[str, Any]):
    """요청 단위 컨텍스트."""

    @property
    def client(self) -> Any:
        return self.get("client")

    @property
    def logger(self) -> Any:
        return self.get("logger")

    @property
    def token(self) -> str | None:
        return self.get("token")

    # Slack Bolt 호환 별칭 — 봇 토큰 하나만 쓰는 구조라 token 과 동일하다.
    @property
    def bot_token(self) -> str | None:
        return self.get("token")

    @property
    def user_id(self) -> str | None:
        return self.get("user_id")

    @property
    def channel_id(self) -> str | None:
        return self.get("channel_id")

    @property
    def team_id(self) -> str | None:
        return self.get("team_id")

    @property
    def bot_user_id(self) -> str | None:
        return self.get("bot_user_id")

    @property
    def bot_id(self) -> str | None:
        return self.get("bot_user_id")

    @property
    def matches(self) -> list[str] | None:
        """``@app.message(re.compile(...))`` 의 캡처 그룹."""
        return self.get("matches")

    @property
    def response_url(self) -> str | None:
        return self.get("response_url")

    @property
    def trigger_id(self) -> str | None:
        return self.get("trigger_id")

    # Slack Bolt 에 있으나 Mattermost 단일 인스턴스에서는 항상 None 인 값들.
    @property
    def enterprise_id(self) -> None:
        return None

    def copy(self) -> BoltContext:
        return BoltContext(self)
