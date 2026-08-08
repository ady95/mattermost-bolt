"""예제 1 — 메시지 리스닝.

Slack Bolt 공식 Getting Started 예제와 **핸들러 본문이 한 글자도 다르지 않다.**
바뀐 것은 import 경로와 ``server_url`` 인자뿐이다.

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    python examples/01_hello_message.py
"""

import logging
import os
import re

from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team=os.environ.get("MM_TEAM"),
)


@app.message("hello")
def handle_hello(message, say):
    say(f"Hey there <@{message['user']}>!")


@app.message(re.compile(r"deploy (\w+)"))
def handle_deploy(context, say):
    environment = context["matches"][0]
    say(f"*{environment}* 배포를 시작합니다.")


@app.event("reaction_added")
def handle_reaction(event, logger):
    logger.info("%s 님이 :%s: 를 달았습니다", event["user"], event["reaction"])


@app.message("thread")
def handle_thread(message, say):
    # 스레드 답장 — Mattermost 의 root_id 로 매핑된다.
    say("스레드로 답합니다.", thread_ts=message["ts"])


if __name__ == "__main__":
    SocketModeHandler(app).start()
