# mattermost-bolt

**Slack Bolt 앱을 초기화 세 줄만 고쳐 Mattermost 로 옮긴다.**

Slack Bolt 기반으로 만든 봇·확장 모듈을 Mattermost 로 마이그레이션할 때,
핸들러 코드를 다시 쓰지 않도록 [Bolt for Python](https://tools.slack.dev/bolt-python/) 의
인터페이스를 최대한 그대로 유지하는 어댑터 레이어입니다.

```python
from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=MM_BOT_TOKEN, server_url="http://mattermost.example.com")


@app.message("hello")
def handle_hello(message, say):
    say(f"Hey there <@{message['user']}>!")


@app.command("/status")
def handle_status(ack, respond):
    ack()
    respond("Status: OK")


SocketModeHandler(app).start()
```

위 코드에서 Slack Bolt 와 다른 부분은 **import 경로와 `server_url` 인자뿐**입니다.

---

## 설치

```bash
pip install mattermost-bolt
# 또는
uv add mattermost-bolt
```

개발 설치:

```bash
git clone https://github.com/ady95/mattermost-bolt
cd mattermost-bolt
uv venv --python 3.13
uv pip install -e ".[dev]"
```

## 5분 Quickstart

### 1. Mattermost 봇 만들기

System Console → Integrations → Bot Accounts 에서 봇을 만들고 **Access Token** 을 발급합니다.
봇을 대상 팀과 채널에 초대해야 이벤트를 받습니다.

```bash
export MM_BOT_TOKEN="..."
export MM_SERVER_URL="https://mattermost.example.com"
```

### 2. 앱 실행

```python
import os
from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    team="bolt",  # 선택 — 채널 이름을 id 로 해석할 때 사용
)


@app.message("hello")
def handle_hello(message, say):
    say(f"Hey there <@{message['user']}>!")


SocketModeHandler(app).start()
```

### 3. 버튼·다이얼로그까지 쓰려면 HTTP 모드

Mattermost 는 인터랙션(버튼 클릭, 다이얼로그 제출)을 **HTTP 로만** 전달합니다.
앱이 수신할 주소를 `request_url` 로 알려주세요.

```python
app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],
    mode="http",
    request_url="http://10.0.0.5:8099",  # Mattermost 가 도달할 수 있는 주소
)
...
app.start(port=8099)
```

**HTTP 모드에서 가장 흔히 막히는 두 지점**입니다. 먼저 확인하세요.

1. Mattermost 가 Docker 안에 있다면 `request_url` 에 `localhost` 를 쓰면 안 됩니다.
   컨테이너 자신을 가리킵니다 — 호스트 LAN IP 또는 `host.docker.internal` 을 쓰세요.
2. Mattermost 는 **사설 IP 로의 아웃바운드 통합 호출을 기본 차단**합니다.
   차단되면 슬래시 명령이 `Command with a trigger of 'x' failed.` 로 실패합니다.

   ```bash
   mmctl config set ServiceSettings.AllowedUntrustedInternalConnections "10.0.0.5"
   ```

   (System Console → Environment → Developer → *Allow untrusted internal connections to*)

자세한 진단은 [MIGRATION.md](MIGRATION.md) 를 참고하세요.

### 4. 기존 WSGI 서버에 얹기 (선택)

내장 HTTP 서버 대신 이미 운영 중인 WSGI 앱(Flask/gunicorn 등)이 인터랙션을 받게 할 수 있습니다.
포트를 하나만 열거나, 기존 인증 미들웨어·리버스 프록시를 그대로 쓰고 싶을 때 유용합니다.

```python
from mattermost_bolt.adapter.http_receiver import wsgi_app

application = wsgi_app(app)  # /mmbolt/* 를 처리한다
app.start(blocking=False, http_receiver=False)  # 내장 리시버 없이 WebSocket 만 기동
```

---

## 예제

[examples/](examples/) 에 기능별 예제 10개가 있습니다. 메시지 리스닝(01)부터
슬래시 명령(02), 버튼·모달(03), 무수정 마이그레이션(04), 리액션 워크플로(05),
스레드 멘션 봇(06), 투표 봇(07), 파일 업로드(08), 채널 온보딩(09),
미들웨어·compat(10)까지 단계적으로 구성되어 있습니다.

각 예제의 실행 요구사항(모드·포트·슬래시 명령 등록)과 예제별 테스트 시나리오는
**[examples/README.md](examples/README.md)** 를 참고하세요.

---

## 두 가지 실행 모드

Slack 은 Socket Mode 하나로 이벤트·명령·액션을 모두 받지만, Mattermost 는 방향이 다릅니다.

```
[이벤트]      Mattermost --(WebSocket push)--> 앱     : posted, reaction_added ...
[명령/액션]   Mattermost --(HTTP POST)------> 앱     : slash command, button, dialog
```

| 모드 | 인바운드 포트 | 지원 리스너 | 용도 |
|---|---|---|---|
| `socket` | 불필요 | `message`, `event`, `command`※ | 개발·PoC·폐쇄망 |
| `http` | 필요 | 전부 | 운영 |

※ `socket` 모드의 `command` 는 WebSocket 으로 받은 `/명령` 텍스트를 파싱해 처리합니다.
Mattermost 에 슬래시 명령을 등록할 필요가 없어 개발이 빠르지만,
`trigger_id` 와 `response_url` 이 없어 **다이얼로그를 열 수 없습니다**.

`mode="auto"`(기본값)는 `@app.action` / `@app.view` 등록 여부와 `request_url` 유무를 보고 판단합니다.
`socket` 모드에서 인터랙티브 리스너를 등록하면 **기동 시 경고**합니다 — 조용히 무시하지 않습니다.

---

## 지원 범위

### 데코레이터

| Slack Bolt | 지원 | Mattermost 소스 |
|---|:--:|---|
| `@app.message(...)` | ✅ | WebSocket `posted` |
| `@app.event("message")` | ✅ | WebSocket `posted` |
| `@app.event("reaction_added")` | ✅ | WebSocket `reaction_added` |
| `@app.event("member_joined_channel")` | ✅ | WebSocket `user_added` |
| `@app.command("/x")` | ✅ | HTTP slash command / WS 텍스트 파싱 |
| `@app.action("id")` | ✅ | HTTP attachment action |
| `@app.view("callback")` | ✅ | HTTP dialog submission |
| `@app.view_closed("callback")` | ✅ | HTTP dialog cancel |
| `@app.use(...)` / `@app.error(...)` | ✅ | 공통 |
| `@app.shortcut(...)` | ⚠️ | 대응 없음 → 동명의 슬래시 명령으로 폴백 |
| `@app.options(...)` | ❌ | v2 예정 |

### 핸들러 인자

`ack`, `say`, `respond`, `client`, `body`, `payload`, `event`, `message`,
`command`, `action`, `view`, `context`, `logger`, `next` — Slack Bolt 와 동일하게
**선언한 것만 주입**됩니다.

### WebClient 메서드

`auth_test`, `chat_postMessage`, `chat_postEphemeral`, `chat_update`, `chat_delete`,
`chat_getPermalink`, `conversations_list/info/open/history/replies/members/join/invite`,
`users_info/list/lookupByEmail`, `reactions_add/remove`, `files_upload_v2`, `views_open`

대응물이 없는 `views_update` / `views_push` / `views_publish` 는
`UnsupportedFeatureError` 로 **명확히 실패**합니다. 조용히 성공한 척하지 않습니다.

### Block Kit

Mattermost 에는 Block Kit 이 없어 **Message Attachments** 와 **Interactive Dialog** 로 변환합니다.

| 블록 | 지원 | 변환 결과 |
|---|:--:|---|
| `section` (text/fields) | ✅ | attachment `text` / `fields` |
| `header` | ✅ | `#### 텍스트` |
| `divider` | ✅ | `---` |
| `context` | ✅ | attachment `footer` |
| `image` | ✅ | attachment `image_url` |
| `actions` (button) | ✅ | attachment `actions[type=button]` |
| `actions` (select) | ✅ | attachment `actions[type=select]` |
| `input` (모달) | ✅ | dialog `elements` |
| `overflow`, `datepicker`, `rich_text` | ⚠️ | 텍스트 폴백 + 경고 로그 |

변환 불가 요소는 **반드시 `logger.warning` 을 남깁니다.** 조용히 사라진 UI 는
마이그레이션에서 가장 찾기 어려운 결함이기 때문입니다.

---

## 마이그레이션 주의점

가장 중요한 하나만 먼저 — **`ts` 를 산술 연산하지 마세요.**

Slack `ts` 는 `"1754620800.123456"` 이지만 Mattermost post id 는 `"tk9c8x..."` 형태입니다.
기본값(`ts_format="post_id"`)에서는 post id 를 그대로 `ts` 자리에 넣으므로,
`ts` 를 불투명한 문자열로만 다루는 코드는 무수정 동작합니다.
`float(ts)` 로 정렬·비교하는 코드가 있다면 `App(ts_format="epoch")` 를 쓰세요.

전체 체크리스트는 [MIGRATION.md](MIGRATION.md) 를 참고하세요.

### import 조차 고치기 싫다면

```python
import mattermost_bolt.compat.install  # 반드시 slack_bolt import 보다 먼저
from slack_bolt import App  # 실제로는 mattermost_bolt.App
```

검증·과도기용 수단입니다. 스택 트레이스가 혼란스러워지므로 운영 코드에서는
`from mattermost_bolt import App` 을 권장합니다.

---

## 개발

```bash
uv run pytest          # 단위 테스트
uv run ruff check .    # 린트
uv run ruff format .   # 포매팅
uv run mypy            # 타입 검사
```

실서버 대상 검증(환경변수 필요):

```bash
export MM_SERVER_URL=https://mattermost.example.com
export MM_BOT_TOKEN=...
export MM_TEST_CHANNEL=bolt-dev

uv run python spikes/s1_websocket.py    # WebSocket 이벤트 수신
uv run python spikes/s2_rest.py         # REST 왕복 (게시·수정·반응·삭제)
uv run python spikes/e2e_smoke.py       # 전체 스모크
```

## 라이선스

MIT
