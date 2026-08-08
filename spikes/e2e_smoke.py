"""E2E 스모크 — 실제 Mattermost 서버 대상 전체 왕복 검증.

단위 테스트는 모의 객체를 쓴다. 이 스크립트는 **진짜 서버**에 붙어
WebSocket 이벤트 수신부터 REST 왕복까지 한 번에 확인한다.

    set -a && . ./dev/.tokens.env && set +a
    uv run python spikes/e2e_smoke.py

종료 코드 0 이면 전부 통과다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from mattermost_bolt import App  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def main() -> int:
    server = os.environ.get("MM_SERVER_URL")
    token = os.environ.get("MM_BOT_TOKEN")
    admin_token = os.environ.get("MM_ADMIN_TOKEN")
    team = os.environ.get("MM_TEAM", "bolt")
    channel_name = os.environ.get("MM_TEST_CHANNEL", "bolt-dev")

    if not (server and token and admin_token):
        print("MM_SERVER_URL / MM_BOT_TOKEN / MM_ADMIN_TOKEN 이 필요합니다.", file=sys.stderr)
        return 2

    app = App(token=token, server_url=server, team=team, mode="socket")
    client = app.client

    print("\n[1] REST 왕복")
    auth = client.auth_test()
    check("auth_test", auth["ok"] and auth["is_bot"], f"@{auth['user']}")

    channel_id = client.resolve_channel_id(channel_name)
    check("채널 이름 → id 해석", len(channel_id) == 26, channel_id)

    posted = client.chat_postMessage(channel=channel_id, text="e2e: *굵게* 확인")
    post_id = posted["ts"]
    check("chat_postMessage", posted["ok"] and len(post_id) == 26, post_id)

    history = client.conversations_history(channel=channel_id, limit=5)
    check(
        "conversations_history",
        any(m["ts"] == post_id for m in history["messages"]),
        f"{len(history['messages'])}건",
    )
    # 서버에 실제로 저장된 본문이 Mattermost 문법으로 변환됐는지 확인한다.
    stored = next(m for m in history["messages"] if m["ts"] == post_id)
    check("mrkdwn 변환 (*굵게* → **굵게**)", "**굵게**" in stored["text"], stored["text"])

    updated = client.chat_update(channel=channel_id, ts=post_id, text="e2e: 수정됨")
    check("chat_update", updated["ok"])

    threaded = client.chat_postMessage(channel=channel_id, text="e2e: 스레드", thread_ts=post_id)
    check(
        "thread_ts → root_id",
        threaded["message"].get("thread_ts") == post_id,
        threaded["message"].get("thread_ts", ""),
    )

    check("reactions_add", client.reactions_add(name="+1", timestamp=post_id)["ok"])
    check("reactions_remove", client.reactions_remove(name="+1", timestamp=post_id)["ok"])

    blocks_post = client.chat_postMessage(
        channel=channel_id,
        blocks=[
            {"type": "header", "text": {"type": "plain_text", "text": "E2E 블록"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*변환* 확인"}},
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "footer"}]},
        ],
    )
    attachments = blocks_post["mattermost_post"]["props"]["attachments"]
    check(
        "Block Kit → attachments",
        attachments[0]["text"].startswith("#### E2E 블록") and attachments[0]["footer"] == "footer",
        attachments[0]["text"][:40].replace("\n", " "),
    )

    users = client.users_info(user=auth["user_id"])
    check("users_info", users["user"]["name"] == auth["user"])

    print("\n[2] WebSocket 이벤트 수신")
    received = threading.Event()
    seen: list[str] = []

    @app.message("e2e-ws-probe")
    def on_probe(message, say):
        seen.append(message["text"])
        say("e2e: 수신 확인", thread_ts=message["ts"])
        received.set()

    app.start(blocking=False)
    connected = app._ws.connected.wait(20)  # noqa: SLF001
    check("WebSocket 인증", connected)

    if connected:
        admin = httpx.Client(
            base_url=f"{server}/api/v4",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=15,
        )
        admin.post("/posts", json={"channel_id": channel_id, "message": "e2e-ws-probe 시작"})
        check("posted 이벤트 → @app.message", received.wait(20), seen[0] if seen else "미수신")

    print("\n[3] 재연결")
    if connected:
        # 연결을 강제로 끊고 자동 복구되는지 본다 (실행계획서 G6).
        app._ws._connection.close()  # noqa: SLF001
        app._ws.connected.clear()  # noqa: SLF001
        check("끊김 후 자동 재연결", app._ws.connected.wait(25))  # noqa: SLF001

    app.stop()
    time.sleep(0.5)

    print("\n" + "=" * 58)
    failed = [name for name, ok, _ in results if not ok]
    print(f"통과 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print("실패:", ", ".join(failed))
    print("=" * 58)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
