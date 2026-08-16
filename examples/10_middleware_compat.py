"""예제 10 — 미들웨어·에러 핸들러, 그리고 무수정 마이그레이션.

이 라이브러리의 존재 이유(핸들러 무수정, 목표 G1)를 증명하는 마무리 예제.
아래 두 가지를 함께 보여준다.

1. ``compat.install`` — import 한 줄로 ``slack_bolt`` import 가 그대로 돈다.
   **아래 코드의 import 문은 Slack Bolt 앱과 동일하다.**
   (검증·과도기용이다. 운영 전에는 ``from mattermost_bolt import App`` 으로
   되돌리는 것을 권장한다 — MIGRATION.md §4)
2. ``@app.use`` 전역 미들웨어, 리스너 미들웨어, ``@app.error`` 전역 에러 핸들러.

    export MM_BOT_TOKEN=... MM_SERVER_URL=https://mattermost.example.com
    python examples/10_middleware_compat.py
"""

import logging
import os
import time

# shim 설치가 slack_bolt import 보다 먼저여야 하므로 정렬을 끈다.
# isort: off
import mattermost_bolt.compat.install  # noqa: F401

# 여기부터는 Slack Bolt 앱의 import 문 그대로다.
from slack_bolt import App  # 실제로는 mattermost_bolt.App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# isort: on

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],  # Mattermost 에만 필요한 인자
    team=os.environ.get("MM_TEAM"),
)


# -- 전역 미들웨어: 모든 요청에 적용된다 --------------------------------------


@app.use
def log_and_time(body, next, logger):
    """요청 타입과 처리 시간을 기록한다. next() 를 불러야 다음으로 넘어간다."""
    started = time.monotonic()
    next()
    elapsed_ms = (time.monotonic() - started) * 1000
    logger.info("[%s] 처리 %.1fms", body.get("type", "?"), elapsed_ms)


# -- 리스너 미들웨어: 특정 핸들러에만 적용된다 --------------------------------


def require_admin(body, client, respond, next):
    """관리자가 아니면 next() 를 부르지 않아 핸들러 실행을 막는다."""
    user = client.users_info(user=body["user_id"])["user"]
    if not user["is_admin"]:
        respond("⛔ 관리자만 사용할 수 있는 명령입니다.")
        return
    next()


@app.command("/admin", middleware=[require_admin])
def admin_only(ack, command):
    ack(f"관리자 명령 실행: {command['text'] or '(인자 없음)'}")


# -- 일반 핸들러 --------------------------------------------------------------


@app.message("핑")
def ping(say):
    say("퐁 🏓")


@app.message("에러")
def boom(message):
    # 전역 에러 핸들러 데모용 — 일부러 실패한다.
    raise RuntimeError(f"의도된 실패 (trigger: {message['text']!r})")


# -- 전역 에러 핸들러 ----------------------------------------------------------


@app.error
def handle_error(error, body, logger):
    """핸들러에서 잡히지 않은 예외가 모두 여기로 온다."""
    logger.exception("처리 실패: %s (body type=%s)", error, body.get("type"))


if __name__ == "__main__":
    SocketModeHandler(app).start()
