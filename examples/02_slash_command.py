"""예제 2 — 슬래시 명령.

같은 핸들러가 두 모드에서 모두 동작한다.

- ``socket`` 모드: Mattermost 에 명령을 등록할 필요가 없다.
  WebSocket 으로 받은 ``/status`` 텍스트를 파싱해 처리한다(결정 D3).
- ``http`` 모드: Mattermost 에 등록한 슬래시 명령이 앱으로 POST 된다.

    # 등록 없이 바로 (개발용)
    MM_BOLT_MODE=socket python examples/02_slash_command.py

    # 정식 경로
    MM_BOLT_MODE=http MM_REQUEST_URL=http://10.0.0.5:8099 \
        python examples/02_slash_command.py
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
    mode=os.environ.get("MM_BOLT_MODE", "auto"),
    request_url=os.environ.get("MM_REQUEST_URL"),
)


@app.command("/status")
def handle_status(ack, command, logger):
    logger.info("%s 님이 /status 실행", command["user_name"])
    ack("Status: OK")


@app.command("/echo")
def handle_echo(ack, command):
    # ack() 는 3초 안에 돌려주는 즉시 응답이다.
    ack(f"입력값: {command['text']}")


@app.command("/slow")
def handle_slow(ack, respond, client, command):
    ack("작업을 시작했습니다…")
    # 오래 걸리는 작업 뒤에는 respond() 로 후속 응답을 보낸다.
    # (socket 모드에는 response_url 이 없어 ephemeral 메시지로 대체된다)
    respond("작업이 끝났습니다.")


if __name__ == "__main__":
    port = int(os.environ.get("MM_APP_PORT", "8099"))
    SocketModeHandler(app, port=port).start()
