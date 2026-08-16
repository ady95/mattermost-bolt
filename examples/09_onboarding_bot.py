"""예제 9 — 채널 온보딩 봇.

팀 운영 봇의 최소 골격. 이벤트만 사용하므로 ``socket`` 모드로 충분하다.

- 누군가 채널에 들어오면 채널에서 환영하고, 상세 안내는 DM 으로 보낸다.
- 새 공개 채널이 만들어지면 봇이 스스로 들어가 인사한다.
- 누군가 나가면 로그만 남긴다.

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    python examples/09_onboarding_bot.py
"""

import logging
import os

from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team=os.environ.get("MM_TEAM"),
)

GUIDE = (
    "환영합니다! 🙌 시작하는 데 도움이 될 안내입니다.\n"
    "- 규칙과 공지는 채널 헤더를 확인하세요.\n"
    "- 질문은 스레드로 남기면 담당자가 답변합니다.\n"
    "- 문의: #support"
)


@app.event("member_joined_channel")
def greet_new_member(event, say, client, logger):
    # 봇 자신의 입장(아래 channel_created 핸들러의 join 포함)은 건너뛴다.
    if event["user"] == client.bot_user_id:
        return

    user = client.users_info(user=event["user"])["user"]
    say(f"<@{event['user']}> ({user['real_name']}) 님, 환영합니다! 👋 안내를 DM 으로 보냈습니다.")

    dm = client.conversations_open(users=event["user"])["channel"]
    client.chat_postMessage(channel=dm["id"], text=GUIDE)
    logger.info("%s 님 온보딩 완료", user["name"])


@app.event("member_left_channel")
def farewell(event, logger):
    logger.info("%s 님이 채널 %s 에서 나갔습니다", event["user"], event["channel"])


@app.event("channel_created")
def join_new_channel(event, client, logger):
    """새 공개 채널이 생기면 봇이 스스로 합류해 인사한다."""
    channel_id = event["channel"]
    try:
        client.conversations_join(channel=channel_id)
    except Exception as error:  # 비공개 채널 등 합류 불가는 조용히 넘어간다.
        logger.info("채널 %s 합류 실패: %s", channel_id, error)
        return

    client.chat_postMessage(
        channel=channel_id,
        text="새 채널 개설을 축하합니다! 🎉 저를 불러야 할 일이 있으면 언제든 멘션하세요.",
    )


if __name__ == "__main__":
    SocketModeHandler(app).start()
