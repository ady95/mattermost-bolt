"""S2~S4 — HTTP 인터랙션 경로 종단 검증.

실행계획서에서 가장 큰 리스크 두 가지를 판정한다.

- **R2**: Mattermost 컨테이너가 호스트에서 도는 앱에 HTTP 로 도달하는가
- **R1**: Team Edition 에서 ``trigger_id`` 로 다이얼로그를 열 수 있는가

전 과정을 API 로 자동화한다(사람의 클릭 불필요).

1. 앱을 http 모드로 기동
2. Mattermost 에 슬래시 명령 등록 (관리자 토큰)
3. ``/api/v4/commands/execute`` 로 명령 실행       → S2
4. Block Kit 버튼이 달린 메시지 게시 후
   ``/api/v4/posts/{id}/actions/{action_id}`` 호출 → S3
5. 명령이 받은 ``trigger_id`` 로 다이얼로그 오픈    → S4

    export MM_SERVER_URL=https://mattermost.example.com
    export MM_BOT_TOKEN=... MM_ADMIN_TOKEN=...
    export MM_REQUEST_URL=http://10.0.0.5:8099   # Mattermost 가 도달할 주소
    uv run python spikes/s2_http.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import httpx

from mattermost_bolt import App

OUT_DIR = Path(__file__).parent / "payloads"
PORT = int(os.environ.get("MM_APP_PORT", "8099"))
TRIGGER = "boltspike"

received: dict[str, Any] = {}
signals = {name: threading.Event() for name in ("command", "action", "dialog")}


def dump(name: str, payload: Any) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"http_{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    server = os.environ.get("MM_SERVER_URL")
    bot_token = os.environ.get("MM_BOT_TOKEN")
    admin_token = os.environ.get("MM_ADMIN_TOKEN")
    request_url = os.environ.get("MM_REQUEST_URL")
    channel_name = os.environ.get("MM_TEST_CHANNEL", "bolt-dev")
    team_name = os.environ.get("MM_TEAM", "bolt")

    if not all([server, bot_token, admin_token, request_url]):
        print(
            "MM_SERVER_URL / MM_BOT_TOKEN / MM_ADMIN_TOKEN / MM_REQUEST_URL 이 필요합니다.",
            file=sys.stderr,
        )
        return 2

    app = App(
        token=bot_token,
        server_url=server,
        team=team_name,
        mode="http",
        request_url=request_url,
    )

    @app.command(f"/{TRIGGER}")
    def on_command(ack, command, context):
        received["command"] = dict(command)
        received["trigger_id"] = command.get("trigger_id")
        received["response_url"] = command.get("response_url")
        dump("command", command)
        ack(f"S2 OK — command={command.get('command')} text={command.get('text')!r}")
        signals["command"].set()

    @app.action("spike_button")
    def on_action(ack, action, body, respond):
        received["action"] = {"action": dict(action), "body": dict(body)}
        dump("action", body)
        ack()
        respond("S3 OK — 버튼 수신")
        signals["action"].set()

    @app.view("spike_dialog")
    def on_view(ack, view, body):
        received["view"] = {"view": dict(view), "body": dict(body)}
        dump("dialog", body)
        ack()
        signals["dialog"].set()

    app.start(port=PORT, blocking=False)
    time.sleep(2.0)

    admin = httpx.Client(
        base_url=f"{server}/api/v4",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )

    team_id = admin.get(f"/teams/name/{team_name}").json()["id"]
    channel_id = admin.get(f"/teams/{team_id}/channels/name/{channel_name}").json()["id"]
    admin_id = admin.get("/users/me").json()["id"]

    # 관리자가 팀·채널에 없으면 명령을 실행할 수 없다.
    admin.post(f"/teams/{team_id}/members", json={"team_id": team_id, "user_id": admin_id})
    admin.post(f"/channels/{channel_id}/members", json={"user_id": admin_id})

    ok = True
    ok &= step_command(app, admin, team_id, channel_id, request_url)
    ok &= step_action(app, admin, channel_id)
    ok &= step_dialog(app, admin, channel_id)

    app.stop()
    print("\n" + "=" * 60)
    print("S2 (slash command → 호스트 도달) :", "PASS" if signals["command"].is_set() else "FAIL")
    print("S3 (버튼 클릭 → 호스트 도달)     :", "PASS" if signals["action"].is_set() else "FAIL")
    print("S4 (다이얼로그 오픈/제출)        :", "PASS" if signals["dialog"].is_set() else "FAIL")
    print("=" * 60)
    return 0 if ok else 1


def step_command(
    app: App, admin: httpx.Client, team_id: str, channel_id: str, request_url: str
) -> bool:
    print("\n--- S2: 슬래시 명령 등록 및 실행 ---")
    existing = admin.get("/commands", params={"team_id": team_id}).json()
    for command in existing if isinstance(existing, list) else []:
        if command.get("trigger") == TRIGGER:
            admin.delete(f"/commands/{command['id']}")

    created = admin.post(
        "/commands",
        json={
            "team_id": team_id,
            "trigger": TRIGGER,
            "method": "P",
            "url": app.command_url,
            "display_name": "Bolt Spike",
            "auto_complete": True,
        },
    )
    if created.status_code >= 300:
        print("명령 등록 실패:", created.status_code, created.text[:300])
        return False
    print(f"등록 완료: /{TRIGGER} → {app.command_url}")

    result = admin.post(
        "/commands/execute",
        json={"channel_id": channel_id, "command": f"/{TRIGGER} hello world"},
    )
    print("execute 응답:", result.status_code, result.text[:200])

    if not signals["command"].wait(15):
        print("!! 앱이 명령을 받지 못했습니다 — 컨테이너에서 호스트로 도달하지 못합니다 (R2)")
        return False
    print("수신 성공. trigger_id =", (received.get("trigger_id") or "")[:20], "...")
    return True


def step_action(app: App, admin: httpx.Client, channel_id: str) -> bool:
    print("\n--- S3: 인터랙티브 버튼 ---")
    blocks = [
        {
            "type": "section",
            "block_id": "spike_block",
            "text": {"type": "mrkdwn", "text": "*S3* 버튼 왕복 검증"},
        },
        {
            "type": "actions",
            "block_id": "spike_block",
            "elements": [
                {
                    "type": "button",
                    "action_id": "spike_button",
                    "text": {"type": "plain_text", "text": "Click me"},
                    "value": "spike-value",
                    "style": "primary",
                }
            ],
        },
    ]
    posted = app.client.chat_postMessage(channel=channel_id, blocks=blocks)
    post_id = posted["ts"]
    print("버튼 메시지 게시:", post_id)

    result = admin.post(f"/posts/{post_id}/actions/spike_button", json={})
    print("action 트리거 응답:", result.status_code, result.text[:200])

    if not signals["action"].wait(15):
        print("!! 앱이 버튼 클릭을 받지 못했습니다")
        return False
    print("수신 성공.")
    return True


def step_dialog(app: App, admin: httpx.Client, channel_id: str) -> bool:
    print("\n--- S4: 다이얼로그 (Team Edition 지원 여부) ---")
    trigger_id = received.get("trigger_id")
    if not trigger_id:
        print("trigger_id 가 없어 건너뜁니다.")
        return False

    view = {
        "type": "modal",
        "callback_id": "spike_dialog",
        "title": {"type": "plain_text", "text": "Spike Dialog"},
        "submit": {"type": "plain_text", "text": "보내기"},
        "private_metadata": json.dumps({"channel": channel_id}),
        "blocks": [
            {
                "type": "input",
                "block_id": "name_block",
                "label": {"type": "plain_text", "text": "이름"},
                "element": {"type": "plain_text_input", "action_id": "name_input"},
            },
            {
                "type": "input",
                "block_id": "note_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "메모"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "note_input",
                    "multiline": True,
                },
            },
        ],
    }
    try:
        opened = app.client.views_open(trigger_id=trigger_id, view=view)
    except Exception as error:
        print("!! views_open 실패:", error)
        return False
    print("views_open 성공 (Team Edition 에서 다이얼로그 지원 확인).")
    state = opened["view"]["state"]

    # 제출은 웹앱이 통합 URL 로 직접 POST 한다. 같은 형태를 앱에 보내 처리 경로를 검증한다.
    submission = {
        "type": "dialog_submission",
        "callback_id": "spike_dialog",
        "state": state,
        "user_id": "spikeuser0000000000000000",
        "channel_id": channel_id,
        "team_id": "",
        "submission": {"e0": "홍길동", "e1": "메모 본문"},
        "cancelled": False,
    }
    response = httpx.post(app.dialog_url, json=submission, timeout=15)
    print("dialog 제출 응답:", response.status_code, response.text[:200])

    if not signals["dialog"].wait(10):
        print("!! 다이얼로그 제출을 처리하지 못했습니다")
        return False
    values = received["view"]["view"]["state"]["values"]
    print("복원된 Slack view state:", json.dumps(values, ensure_ascii=False))
    assert values["name_block"]["name_input"]["value"] == "홍길동", values
    print("block_id/action_id 왕복 복원 확인.")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
