"""예제 3-서버 — ``03_interactive.py`` 를 FastAPI 위에 올린 버전.

**리스너 코드는 03_interactive.py 와 완전히 동일하다.** 달라지는 것은
HTTP 를 누가 받느냐뿐이다.

    03_interactive.py        내장 ThreadingHTTPServer 가 받는다
    03_interactive_server.py FastAPI(uvicorn) 가 받아 App.handle_* 로 넘긴다

이미 FastAPI 서비스를 운영 중이라 포트를 하나만 열고 싶거나, 인증 미들웨어·
리버스 프록시·헬스체크 등 기존 웹 스택을 그대로 쓰고 싶을 때 이 형태를 쓴다.

    pip install "mattermost-bolt[fastapi]"

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    export MM_REQUEST_URL=http://10.0.0.5:8099   # Mattermost 가 도달할 주소
    uvicorn examples.03_interactive_server:api --host 0.0.0.0 --port 8099

    # 또는 그냥
    python examples/03_interactive_server.py

Mattermost 에 등록할 슬래시 명령 Request URL:
    {MM_REQUEST_URL}/mmbolt/commands
"""

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from mattermost_bolt import App, BoltResponse
from mattermost_bolt.adapter.http_receiver import parse_body

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team=os.environ.get("MM_TEAM"),
    mode="http",
    request_url=os.environ["MM_REQUEST_URL"],
)


# ══════════════════════════════════════════════════════════════════
# 리스너 — 03_interactive.py 와 동일하다.
# ══════════════════════════════════════════════════════════════════


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
    ack()
    client.views_open(
        trigger_id=command["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "feedback_modal",
            "title": {"type": "plain_text", "text": "피드백"},
            "submit": {"type": "plain_text", "text": "보내기"},
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


# ══════════════════════════════════════════════════════════════════
# FastAPI 연결부
# ══════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(_: FastAPI):
    """WebSocket 리스너를 서버 수명주기에 묶는다.

    ``http_receiver=False`` 로 내장 HTTP 서버는 띄우지 않는다.
    인터랙션은 아래 라우트가 받아 ``App.handle_*`` 로 넘긴다.
    """
    app.start(blocking=False, http_receiver=False)
    try:
        yield
    finally:
        app.stop()


api = FastAPI(title="mattermost-bolt + FastAPI", lifespan=lifespan)


def _to_response(bolt_response: BoltResponse) -> Response:
    """``BoltResponse`` → FastAPI ``Response``."""
    payload = bolt_response.to_bytes()
    return Response(
        content=payload,
        status_code=bolt_response.status,
        headers=bolt_response.headers,
    )


async def _read(request: Request) -> dict:
    """Mattermost 는 명령을 폼으로, 인터랙션을 JSON 으로 보낸다.

    ``parse_body`` 가 둘 다 처리하므로 python-multipart 의존성이 필요 없다.
    """
    return parse_body(request.headers.get("content-type", ""), await request.body())


# 리스너는 동기 함수이고 내부에서 Mattermost REST 를 호출한다(블로킹).
# 이벤트 루프를 막지 않도록 스레드풀에서 실행한다.
@api.post("/mmbolt/commands")
async def commands(request: Request) -> Response:
    return _to_response(await run_in_threadpool(app.handle_command, await _read(request)))


@api.post("/mmbolt/actions")
async def actions(request: Request) -> Response:
    return _to_response(await run_in_threadpool(app.handle_action, await _read(request)))


@api.post("/mmbolt/dialogs")
async def dialogs(request: Request) -> Response:
    return _to_response(await run_in_threadpool(app.handle_dialog, await _read(request)))


@api.get("/mmbolt/health")
async def health() -> dict:
    """WebSocket 연결 상태까지 함께 보고한다."""
    connected = bool(app._ws and app._ws.connected.is_set())
    return {"ok": True, "websocket": "connected" if connected else "disconnected"}


# 기존 서비스의 라우트를 같은 앱에 계속 둘 수 있다.
@api.get("/")
async def index() -> dict:
    return {"service": "mattermost-bolt example", "endpoints": ["/mmbolt/health"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(api, host="0.0.0.0", port=int(os.environ.get("MM_APP_PORT", "8099")))
