"""예제 3 — Block Kit 버튼과 모달(다이얼로그).

Slack Block Kit JSON 을 그대로 넣는다. mattermost-bolt 가 Mattermost 의
Message Attachments / Interactive Dialog 로 변환한다.

인터랙션은 Mattermost 가 HTTP 로만 전달하므로 **http 모드가 필수**다.

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    export MM_REQUEST_URL=http://10.0.0.5:8099   # Mattermost 가 도달할 주소
    python examples/03_interactive.py

Mattermost 가 컨테이너 안에 있다면 두 가지를 확인하세요.
1. request_url 이 컨테이너에서 도달 가능한 주소인가 (localhost 금지)
2. System Console → Developer → "Allow untrusted internal connections" 에
   해당 호스트가 등록되어 있는가 (사설 IP 는 기본 차단됨)
"""

import json
import logging
import os

from mattermost_bolt import App

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team=os.environ.get("MM_TEAM"),
    mode="http",
    request_url=os.environ["MM_REQUEST_URL"],
)


@app.command("/approve")
def show_buttons(ack, command, client):
    ack()
    client.chat_postMessage(
        channel=command["channel_id"],
        blocks=[
            {
                "type": "section",
                "block_id": "request",
                "text": {"type": "mrkdwn", "text": f"*{command['text']}* 승인 요청"},
            },
            {
                "type": "actions",
                "block_id": "request",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve",
                        "text": {"type": "plain_text", "text": "승인"},
                        "style": "primary",
                        "value": command["text"],
                    },
                    {
                        "type": "button",
                        "action_id": "reject",
                        "text": {"type": "plain_text", "text": "반려"},
                        "style": "danger",
                        "value": command["text"],
                    },
                ],
            },
        ],
    )


@app.action("approve")
def on_approve(ack, action, body, respond):
    ack()
    respond(f"✅ *{action['value']}* 승인 — <@{body['user']['id']}>", replace_original=True)


@app.action("reject")
def on_reject(ack, respond):
    ack()
    respond("❌ 반려했습니다.", replace_original=True)


@app.command("/feedback")
def open_modal(ack, command, client):
    """slash command 의 trigger_id 로 모달을 연다."""
    ack()
    client.views_open(
        trigger_id=command["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "feedback_modal",
            "title": {"type": "plain_text", "text": "피드백"},
            "submit": {"type": "plain_text", "text": "보내기"},
            # private_metadata 는 제출 시 그대로 돌아온다.
            "private_metadata": json.dumps({"channel": command["channel_id"]}),
            "blocks": [
                {
                    "type": "input",
                    "block_id": "topic",
                    "label": {"type": "plain_text", "text": "주제"},
                    "element": {
                        "type": "static_select",
                        "action_id": "value",
                        "options": [
                            {"text": {"type": "plain_text", "text": "버그"}, "value": "bug"},
                            {"text": {"type": "plain_text", "text": "제안"}, "value": "idea"},
                        ],
                    },
                },
                {
                    "type": "input",
                    "block_id": "detail",
                    "label": {"type": "plain_text", "text": "내용"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "value",
                        "multiline": True,
                    },
                },
            ],
        },
    )


@app.view("feedback_modal")
def on_submit(ack, view, body, client):
    values = view["state"]["values"]
    detail = values["detail"]["value"]["value"]

    if len(detail.strip()) < 5:
        # Slack 과 동일한 검증 오류 응답.
        ack(response_action="errors", errors={"detail": "5자 이상 입력하세요."})
        return

    ack()
    channel = json.loads(view["private_metadata"])["channel"]
    topic = values["topic"]["value"]["selected_option"]["value"]
    client.chat_postMessage(
        channel=channel,
        text=f"[{topic}] <@{body['user']['id']}> 님의 피드백:\n> {detail}",
    )


@app.view_closed("feedback_modal")
def on_cancel(ack, logger):
    ack()
    logger.info("피드백 입력이 취소되었습니다")


if __name__ == "__main__":
    app.start(port=int(os.environ.get("MM_APP_PORT", "8099")))
