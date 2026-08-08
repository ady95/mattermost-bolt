"""``SlackResponse`` 호환 응답 객체.

기존 Bolt 앱은 ``resp["ts"]``, ``resp["channel"]``, ``resp.get("ok")``,
``resp.data`` 형태로 응답을 다룬다. 같은 인터페이스를 유지한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..errors import MattermostApiError


class MattermostResponse:
    """dict 처럼 동작하는 API 응답 래퍼."""

    def __init__(
        self,
        *,
        data: dict[str, Any] | None = None,
        status_code: int = 200,
        api_url: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.data: dict[str, Any] = dict(data or {})
        self.status_code = status_code
        self.api_url = api_url
        self.headers = headers or {}
        # Slack 응답의 ``ok`` 는 사실상 모든 앱이 검사하므로 항상 채워 넣는다.
        self.data.setdefault("ok", 200 <= status_code < 300)

    # -- dict 호환 ----------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __contains__(self, key: object) -> bool:
        return key in self.data

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def keys(self):
        return self.data.keys()

    def items(self):
        return self.data.items()

    def values(self):
        return self.data.values()

    # -- Slack SDK 호환 -----------------------------------------------------

    def validate(self) -> MattermostResponse:
        """실패 응답이면 예외를 던진다 (``SlackResponse.validate`` 대응)."""
        if not self.data.get("ok"):
            raise MattermostApiError(
                f"The request to {self.api_url} failed (status: {self.status_code})",
                self,
            )
        return self

    def __repr__(self) -> str:  # pragma: no cover - 디버깅용
        return f"<MattermostResponse status={self.status_code} keys={list(self.data)[:6]}>"


# Slack SDK 호환 별칭.
SlackResponse = MattermostResponse
