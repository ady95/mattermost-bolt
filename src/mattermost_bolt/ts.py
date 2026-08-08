"""``ts`` 표현 코덱 — 실행계획서 결정 D4.

Slack 의 ``ts`` 는 ``"1754620800.123456"`` 형태의 문자열이고
Mattermost 의 post id 는 26자 영숫자 문자열이다.

- ``post_id`` 모드(기본): ``ts`` 자리에 post id 를 그대로 넣는다.
  ``ts`` 를 불투명한 문자열로만 다루는 대부분의 앱이 무수정 동작한다.
- ``epoch`` 모드: ``create_at``(ms)을 Slack 형식으로 변환한다.
  ``float(ts)`` 로 시간 비교/정렬을 하는 앱을 위한 옵션이며,
  변환된 값에서 post id 를 되찾기 위해 양방향 매핑을 유지한다.
"""

from __future__ import annotations

from collections import OrderedDict

TsFormat = str  # "post_id" | "epoch"

POST_ID_LENGTH = 26


def looks_like_post_id(value: str) -> bool:
    """26자 영숫자면 Mattermost id 로 본다."""
    return len(value) == POST_ID_LENGTH and value.isalnum()


class TsCodec:
    """post id ↔ ``ts`` 상호 변환기.

    ``epoch`` 모드에서만 매핑 캐시를 사용한다. 캐시는 LRU 로 제한되며,
    캐시 미스 시에는 입력값을 그대로 돌려준다(호출자가 실제 post id 를
    직접 넘긴 경우를 지원하기 위함).
    """

    def __init__(self, mode: TsFormat = "post_id", maxsize: int = 10_000) -> None:
        if mode not in ("post_id", "epoch"):
            raise ValueError(f"unknown ts_format: {mode!r} (post_id | epoch)")
        self.mode = mode
        self._maxsize = maxsize
        self._ts_to_id: OrderedDict[str, str] = OrderedDict()
        self._id_to_ts: OrderedDict[str, str] = OrderedDict()

    # -- 변환 ---------------------------------------------------------------

    def encode(self, post_id: str, create_at: int | None = None) -> str:
        """post id → ``ts``."""
        if self.mode == "post_id" or not post_id:
            return post_id
        if create_at is None:
            # 시각을 모르면 변환할 수 없다. post id 를 그대로 쓴다.
            return post_id
        ts = f"{create_at // 1000}.{(create_at % 1000) * 1000:06d}"
        self._remember(ts, post_id)
        return ts

    def encode_post(self, post: dict) -> str:
        """Mattermost post dict → ``ts``."""
        return self.encode(post.get("id", ""), post.get("create_at"))

    def decode(self, ts: str | None) -> str | None:
        """``ts`` → post id. 알 수 없으면 입력값을 그대로 돌려준다."""
        if not ts:
            return ts
        if self.mode == "post_id":
            return ts
        return self._ts_to_id.get(ts, ts)

    def ts_for(self, post_id: str) -> str | None:
        """이미 발급한 ``ts`` 조회 (없으면 None)."""
        if self.mode == "post_id":
            return post_id
        return self._id_to_ts.get(post_id)

    # -- 내부 ---------------------------------------------------------------

    def _remember(self, ts: str, post_id: str) -> None:
        self._ts_to_id[ts] = post_id
        self._ts_to_id.move_to_end(ts)
        self._id_to_ts[post_id] = ts
        self._id_to_ts.move_to_end(post_id)
        while len(self._ts_to_id) > self._maxsize:
            old_ts, old_id = self._ts_to_id.popitem(last=False)
            self._id_to_ts.pop(old_id, None)
            del old_ts
