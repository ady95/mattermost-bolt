"""예제 7 — 투표(Poll) 봇.

버튼 클릭이 있을 때마다 원본 메시지를 다시 그리는, **상태를 가진 인터랙션**
예제다. 예제 3 이 "버튼이 눌린다"까지 보여줬다면, 여기서는 눌린 결과를
집계해 ``respond(replace_original=True)`` 로 메시지를 갱신한다.

    /poll 점심 뭐 먹을까요?; 짜장면; 짬뽕; 볶음밥
    /poll 배포 진행할까요?              ← 옵션 생략 시 👍/👎

인터랙션은 HTTP 로만 전달되므로 **http 모드 필수**다(예제 3 과 동일).

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    export MM_REQUEST_URL=http://10.0.0.5:8099   # Mattermost 가 도달할 주소
    python examples/07_poll_bot.py

투표 상태는 데모를 위해 메모리 dict 에 둔다. 프로세스를 재시작하면
진행 중이던 투표의 버튼은 동작하지 않는다.
"""

import logging
import os
import re
import uuid

from mattermost_bolt import App

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team=os.environ.get("MM_TEAM"),
    mode="http",
    request_url=os.environ["MM_REQUEST_URL"],
)

MAX_OPTIONS = 5

# poll_id → {"question", "options", "votes": {user_id: option_index}, "creator"}
POLLS: dict[str, dict] = {}


def render_blocks(poll_id: str, poll: dict, *, closed: bool = False) -> list[dict]:
    """현재 집계를 Block Kit 으로 그린다. 매 클릭마다 통째로 다시 그린다."""
    counts = [0] * len(poll["options"])
    for idx in poll["votes"].values():
        counts[idx] += 1

    lines = []
    for idx, option in enumerate(poll["options"]):
        bar = "█" * counts[idx]
        lines.append(f"{idx + 1}. {option} — *{counts[idx]}표* {bar}")

    title = f"📊 *{poll['question']}*" + (" (마감)" if closed else "")
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": title}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_총 {len(poll['votes'])}명 참여 · 1인 1표_"}],
        },
    ]
    if not closed:
        buttons = [
            {
                "type": "button",
                "action_id": f"vote_{idx}",
                "text": {"type": "plain_text", "text": option},
                "value": f"{poll_id}:{idx}",
            }
            for idx, option in enumerate(poll["options"])
        ]
        buttons.append(
            {
                "type": "button",
                "action_id": "poll_close",
                "text": {"type": "plain_text", "text": "마감"},
                "style": "danger",
                "value": poll_id,
            }
        )
        blocks.append({"type": "actions", "block_id": poll_id, "elements": buttons})
    return blocks


@app.command("/poll")
def create_poll(ack, command, client):
    parts = [p.strip() for p in command["text"].split(";") if p.strip()]
    if not parts:
        ack("사용법: `/poll 질문; 옵션1; 옵션2` (옵션 생략 시 👍/👎)")
        return

    question, options = parts[0], parts[1:] or ["👍 찬성", "👎 반대"]
    if len(options) > MAX_OPTIONS:
        ack(f"옵션은 최대 {MAX_OPTIONS}개까지 가능합니다.")
        return

    ack()
    poll_id = uuid.uuid4().hex[:8]
    poll = {
        "question": question,
        "options": options,
        "votes": {},
        "creator": command["user_id"],
    }
    POLLS[poll_id] = poll
    client.chat_postMessage(
        channel=command["channel_id"],
        text=f"📊 {question}",
        blocks=render_blocks(poll_id, poll),
    )


@app.action(re.compile(r"^vote_\d+$"))
def on_vote(ack, action, body, respond):
    ack()
    poll_id, idx = action["value"].split(":")
    poll = POLLS.get(poll_id)
    if poll is None:
        respond("이미 종료되었거나 알 수 없는 투표입니다.")
        return

    # 같은 사람이 다시 누르면 표를 옮긴다(1인 1표).
    poll["votes"][body["user"]["id"]] = int(idx)
    respond(
        text=f"📊 {poll['question']}",
        blocks=render_blocks(poll_id, poll),
        replace_original=True,
    )


@app.action("poll_close")
def on_close(ack, action, body, respond):
    ack()
    poll = POLLS.get(action["value"])
    if poll is None:
        respond("이미 종료되었거나 알 수 없는 투표입니다.")
        return
    if body["user"]["id"] != poll["creator"]:
        respond("투표를 만든 사람만 마감할 수 있습니다.")
        return

    respond(
        text=f"📊 {poll['question']} (마감)",
        blocks=render_blocks(action["value"], poll, closed=True),
        replace_original=True,
    )
    del POLLS[action["value"]]


if __name__ == "__main__":
    app.start(port=int(os.environ.get("MM_APP_PORT", "8099")))
