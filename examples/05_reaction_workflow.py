"""예제 5 — 리액션 기반 워크플로.

이모지 리액션을 트리거로 쓰는 봇. 이벤트만 사용하므로 포트 개방 없이
``socket`` 모드로 동작한다.

- ✅ (`white_check_mark`) : 해당 메시지를 "승인 처리" 하고 스레드로 알린 뒤
  봇이 답 리액션을 단다.
- 📌 (`pushpin`) : 해당 메시지의 permalink 를 리액션한 사람의 DM 으로
  보내주는 북마크 기능.

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    python examples/05_reaction_workflow.py
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


@app.event("reaction_added")
def on_reaction_added(event, client, say, logger):
    # 봇 자신이 단 리액션은 무시한다 — 아래에서 reactions_add 를 호출하므로
    # 이 가드가 없으면 자기 이벤트에 다시 반응할 수 있다.
    if event["user"] == client.bot_user_id:
        return

    if event["reaction"] == "white_check_mark":
        approve(event, client, say)
    elif event["reaction"] == "pushpin":
        bookmark(event, client)
    else:
        logger.info("리액션 무시: :%s:", event["reaction"])


def approve(event, client, say):
    """✅ — 메시지를 승인 처리하고 스레드에 기록을 남긴다."""
    target_ts = event["item"]["ts"]
    say(
        f"<@{event['user']}> 님이 이 메시지를 승인했습니다. ✅",
        thread_ts=target_ts,
    )
    # 봇도 같은 메시지에 답 리액션을 단다.
    client.reactions_add(name="robot_face", timestamp=target_ts)


def bookmark(event, client):
    """📌 — 메시지 permalink 를 리액션한 사람의 DM 으로 보낸다."""
    permalink = client.chat_getPermalink(message_ts=event["item"]["ts"])["permalink"]
    dm = client.conversations_open(users=event["user"])["channel"]
    client.chat_postMessage(
        channel=dm["id"],
        text=f"📌 북마크한 메시지입니다.\n{permalink}",
    )


@app.event("reaction_removed")
def on_reaction_removed(event, logger):
    logger.info("%s 님이 :%s: 를 제거했습니다", event["user"], event["reaction"])


if __name__ == "__main__":
    SocketModeHandler(app).start()
