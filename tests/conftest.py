"""공용 테스트 픽스처."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from mattermost_bolt import App
from mattermost_bolt.ts import TsCodec
from mattermost_bolt.web.response import MattermostResponse


class FakeClient:
    """네트워크를 타지 않는 ``WebClient`` 대역.

    호출 기록을 남겨 리스너가 무엇을 했는지 검증한다.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.action_url = "http://app.test/mmbolt/actions"
        self.logger = logging.getLogger("test.client")
        self.ts_codec = TsCodec()

    def _record(self, method: str, **kwargs: Any) -> MattermostResponse:
        self.calls.append({"method": method, **kwargs})
        return MattermostResponse(
            data={"ok": True, "ts": "post_id_0000000000000000", "channel": kwargs.get("channel")},
            api_url="http://app.test",
        )

    def chat_postMessage(self, **kwargs: Any) -> MattermostResponse:
        return self._record("chat_postMessage", **kwargs)

    def chat_postEphemeral(self, **kwargs: Any) -> MattermostResponse:
        return self._record("chat_postEphemeral", **kwargs)

    def chat_update(self, **kwargs: Any) -> MattermostResponse:
        return self._record("chat_update", **kwargs)

    def respond_to_url(self, url: str, body: dict[str, Any]) -> MattermostResponse:
        return self._record("respond_to_url", url=url, body=body)

    def auth_test(self, **kwargs: Any) -> MattermostResponse:
        return MattermostResponse(
            data={"ok": True, "user_id": "botuser00000000000000000a", "user": "boltbot"}
        )

    def of(self, method: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["method"] == method]

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def app(fake_client: FakeClient) -> App:
    """WebSocket/HTTP 를 띄우지 않는 App. 디스패치만 검증한다."""
    instance = App(
        token="token00000000000000000000",
        server_url="http://mm.test",
        client=fake_client,  # type: ignore[arg-type]
        mode="socket",
        request_url="http://app.test",
    )
    # 자기 메시지 무시 로직을 테스트에서 쓰기 위해 봇 id 를 미리 심는다.
    instance._bot_user_id = "botuser00000000000000000a"
    return instance


@pytest.fixture
def http_app(fake_client: FakeClient) -> App:
    instance = App(
        token="token00000000000000000000",
        server_url="http://mm.test",
        client=fake_client,  # type: ignore[arg-type]
        mode="http",
        request_url="http://app.test",
    )
    instance._bot_user_id = "botuser00000000000000000a"
    return instance


def drain(app: App) -> None:
    """WebSocket 경로는 스레드풀로 디스패치된다. 완료를 기다린다."""
    app._executor.shutdown(wait=True)
