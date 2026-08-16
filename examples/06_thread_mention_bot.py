"""예제 6 — 스레드 멘션 Q&A 봇.

Mattermost 에는 Slack 의 ``app_mention`` 이벤트가 없다(MIGRATION.md §2.7).
공식 대체 패턴대로 **봇 이름 정규식 매칭**으로 멘션을 감지하고,
``thread_ts`` → ``root_id`` 매핑으로 스레드 안에서 대화를 이어간다.

- ``@boltbot 안녕`` : 멘션을 감지해 스레드로 응답
- 스레드 안에서 ``요약`` : ``conversations_replies`` 로 스레드 전체를 읽어
  메시지 수·참여자를 집계해 스레드로 회신

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    export MM_BOT_NAME=boltbot        # 봇 계정의 username
    python examples/06_thread_mention_bot.py
"""

import logging
import os
import re

from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)

BOT_NAME = os.environ.get("MM_BOT_NAME", "boltbot")

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team=os.environ.get("MM_TEAM"),
)


@app.message(re.compile(rf"@{BOT_NAME}\b"))
def on_mention(message, say):
    """Slack 의 ``@app.event("app_mention")`` 에 해당하는 부분."""
    # 멘션받은 메시지가 이미 스레드면 그 스레드로, 아니면 새 스레드를 연다.
    root = message.get("thread_ts") or message["ts"]
    say(
        f"<@{message['user']}> 부르셨나요?\n"
        "이 스레드에서 `요약` 이라고 입력하면 스레드를 정리해 드립니다.",
        thread_ts=root,
    )


@app.message(re.compile(r"^요약$"))
def summarize_thread(message, say, client):
    root = message.get("thread_ts")
    if not root:
        say("스레드 안에서만 요약할 수 있습니다. 스레드 답글로 `요약` 을 입력하세요.")
        return

    replies = client.conversations_replies(channel=message["channel"], ts=root)
    messages = [m for m in replies["messages"] if m["text"].strip() != "요약"]

    participants = []
    for m in messages:
        if m["user"] not in participants:
            participants.append(m["user"])
    names = [client.users_info(user=u)["user"]["name"] for u in participants]

    first = messages[0]["text"] if messages else ""
    preview = first if len(first) <= 60 else first[:60] + "…"
    say(
        f"**스레드 요약**\n"
        f"- 시작: {preview}\n"
        f"- 메시지 {len(messages)}개 / 참여자 {len(names)}명 ({', '.join(names)})",
        thread_ts=root,
    )


if __name__ == "__main__":
    SocketModeHandler(app).start()
