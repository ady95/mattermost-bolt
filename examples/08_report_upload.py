"""예제 8 — 리포트 생성과 파일 업로드.

``files_upload_v2`` 시연. ``/report`` 를 입력하면 채널의 최근 대화를 읽어
사용자별 메시지 수를 집계한 CSV 를 만들어 채널에 업로드한다.

파일 업로드는 클라이언트(REST) 호출이므로 슬래시 명령만 받을 수 있으면
어느 모드에서든 동작한다. ``socket`` 모드라면 Mattermost 에 명령을 등록할
필요도 없다(예제 2 와 동일한 원리).

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    python examples/08_report_upload.py
"""

import csv
import io
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

HISTORY_LIMIT = 50


@app.command("/report")
def make_report(ack, command, client, respond, logger):
    ack("리포트를 생성하는 중입니다…")

    channel = command["channel_id"]
    history = client.conversations_history(channel=channel, limit=HISTORY_LIMIT)

    counts: dict[str, int] = {}
    for message in history["messages"]:
        user = message.get("user")
        if not user or message.get("subtype") == "bot_message":
            continue
        counts[user] = counts.get(user, 0) + 1

    if not counts:
        respond("집계할 메시지가 없습니다.")
        return

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["username", "real_name", "message_count"])
    for user_id, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        user = client.users_info(user=user_id)["user"]
        writer.writerow([user["name"], user["real_name"], count])

    upload = client.files_upload_v2(
        channel=channel,
        content=buffer.getvalue(),
        filename="channel_report.csv",
        initial_comment=(
            f"최근 {HISTORY_LIMIT}개 메시지 기준 사용자별 활동 리포트입니다. "
            f"(요청: @{command['user_name']})"
        ),
    )
    logger.info("리포트 업로드 완료: file_id=%s", upload["file"].get("id"))

    # 요청자에게만 보이는 완료 알림.
    respond(f"리포트가 업로드되었습니다. ({len(counts)}명 집계)")


if __name__ == "__main__":
    SocketModeHandler(app).start()
