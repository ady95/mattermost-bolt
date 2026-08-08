"""예제 4 — 실제 Slack Bolt 앱을 옮긴 모습.

이 파일의 **초기화 블록 아래는 Slack 앱에서 그대로 복사한 코드**다.
마이그레이션에서 무엇을 고쳐야 하는지 한눈에 보이도록 경계를 표시했다.

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    python examples/04_migrated_slack_app.py
"""

import logging
import os

# ┌─────────────────────────────────────────────────────────────────┐
# │ 여기까지만 바꾼다                                                │
# │                                                                 │
# │ 원본(Slack):                                                    │
# │   from slack_bolt import App                                    │
# │   from slack_bolt.adapter.socket_mode import SocketModeHandler  │
# │   app = App(token=os.environ["SLACK_BOT_TOKEN"])                │
# └─────────────────────────────────────────────────────────────────┘
from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],  # Mattermost 에만 필요한 인자
    team=os.environ.get("MM_TEAM"),
)

# ══════════════════════════════════════════════════════════════════
# 아래는 원본 Slack 앱 코드 그대로 — 한 줄도 수정하지 않았다.
# ══════════════════════════════════════════════════════════════════

WELCOME = "환영합니다! `/help` 로 사용법을 확인하세요."


@app.middleware
def log_request(logger, body, next):
    logger.debug("수신: %s", body.get("type"))
    next()


@app.event("member_joined_channel")
def greet_new_member(event, say):
    say(f"<@{event['user']}> {WELCOME}")


@app.message("도움말")
def show_help(say):
    say(
        blocks=[
            {"type": "header", "text": {"type": "plain_text", "text": "사용 가능한 명령"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": "*도움말*\n이 안내를 봅니다"},
                    {"type": "mrkdwn", "text": "*상태*\n서비스 상태를 봅니다"},
                ],
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "_문의: #support_"}],
            },
        ]
    )


@app.message("상태")
def show_status(say, client, message):
    user = client.users_info(user=message["user"])["user"]
    say(f"안녕하세요 *{user['real_name']}* 님. 모든 시스템이 정상입니다.")


@app.event("reaction_added")
def on_reaction(event, client, logger):
    if event["reaction"] != "eyes":
        return
    logger.info("👀 반응: %s", event["item"]["ts"])
    client.chat_postMessage(
        channel=event["item"]["channel"],
        text="확인 중입니다…",
        thread_ts=event["item"]["ts"],
    )


@app.error
def handle_error(error, body, logger):
    logger.exception("처리 실패: %s", error)


if __name__ == "__main__":
    SocketModeHandler(app).start()
