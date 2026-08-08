# Mattermost Bolt 실행계획서

> 작성일: 2026-08-08
> 대상: Slack Bolt 기반 앱을 Mattermost로 최소 수정 마이그레이션하기 위한 어댑터 라이브러리
> 관련 문서: `mattermost-bolt_아이디어.md`, `우분투서버_SSH&Mattermost설치정보.txt`

---

## 1. 목표와 성공 기준

### 1.1 한 줄 정의

**`slack_bolt` 를 `mattermost_bolt` 로 바꿔 import 하는 것만으로 기존 Slack 앱이 Mattermost에서 동작하게 한다.**

### 1.2 성공 기준 (Definition of Done)

| # | 기준 | 측정 방법 |
|---|---|---|
| G1 | 기존 Bolt 앱의 **핸들러 본문 코드 수정 0줄** | 마이그레이션 검증 앱 2종의 diff가 import 문·초기화 블록에만 존재 |
| G2 | `message` / `event` / `command` / `action` / `view` 5종 데코레이터 동작 | E2E 시나리오 스크립트 전부 통과 |
| G3 | 핸들러 인자 주입(`say`, `ack`, `respond`, `client`, `body`, `context`, `logger`) 동등 | 단위 테스트 |
| G4 | Block Kit 입력을 받아 Mattermost에 렌더 (핵심 블록 한정) | 시각 확인 + 스냅샷 테스트 |
| G5 | Team Edition 11.10.0 에서 전 기능 동작 (유료 기능 의존 0) | 대상 서버에서 검증 |
| G6 | 프로세스 재시작 후 WebSocket 자동 재연결, 이벤트 유실 없음 | 크래시 복구 테스트 |

### 1.3 명시적 비목표 (Out of Scope, v1)

- Slack Workflow Builder / Steps from Apps
- Slack 유료 전용 기능(Enterprise Grid, Discovery API)
- Home Tab (Mattermost에 대응 개념 없음 — v2에서 봇 DM 채널로 에뮬레이션 검토)
- OAuth 다중 워크스페이스 설치 흐름 (`OAuthFlow`, `InstallationStore`) — v1은 단일 인스턴스 봇 토큰 전용

---

## 2. 핵심 설계 결정

### 2.1 결정 D1 — 언어·스택: Python 3.13 + uv

`bolt-python` 인터페이스를 1:1로 모사하는 것이 목표이므로 Python으로 간다.
대상 서버가 이미 `uv` + Python 3.13.14 로 `channel-bridge` 프로젝트를 운영 중이라 개발 환경 재사용이 가능하다.

- 패키지명: `mattermost-bolt` / import 명: `mattermost_bolt`
- 의존성 최소화: `websockets`, `httpx`, `flask`(선택) 정도. `mattermostdriver` 는 **의존하지 않는다**
  (동기 전용·유지보수 정체 이슈. REST/WS는 직접 얇게 구현하고, 필요한 부분만 참고)

### 2.2 결정 D2 — 이벤트 수신은 WebSocket, 인터랙션 수신은 HTTP (이중 채널)

Slack Bolt는 Socket Mode 하나로 이벤트·명령·액션을 모두 받지만, Mattermost는 방향이 다르다.

```
[이벤트]      Mattermost --(WebSocket outbound push)--> 앱     : posted, post_edited, reaction_added ...
[명령/액션]   Mattermost --(HTTP POST to request_url)--> 앱     : slash command, button, dialog submit
```

따라서 어댑터는 **두 개의 수신 경로를 하나의 리스너 레지스트리로 합류**시킨다.

```
       ┌──────────────┐
MM WS  │ WSListener   │──┐
       └──────────────┘  │   ┌─────────────────┐   ┌──────────────┐
                         ├──►│ Normalizer      │──►│ App.dispatch │──► listener
       ┌──────────────┐  │   │ (MM → Slack형)  │   │ + middleware │
MM HTTP│ HTTPReceiver │──┘   └─────────────────┘   └──────────────┘
       └──────────────┘
```

### 2.3 결정 D3 — HTTP 없이도 돌아가는 "Pseudo Socket Mode" 를 제공한다

Slack Socket Mode의 최대 이점은 **인바운드 포트 개방이 불필요**하다는 점이다. Mattermost에는 대응 기능이 없지만,
봇은 자신이 속한 채널의 모든 `posted` 이벤트를 WebSocket으로 받으므로 **텍스트 기반 명령을 WS에서 파싱**할 수 있다.

| 모드 | 구현 | 지원 범위 | 용도 |
|---|---|---|---|
| `socket` (기본) | WS만 사용, `/cmd` 텍스트를 파싱해 `@app.command` 로 라우팅 | message, event, command | 개발·PoC·폐쇄망 |
| `http` | WS + HTTP 리시버 | 전체 (action, view 포함) | 운영 |

`app.start()` 시 등록된 리스너 종류를 보고 필요한 모드를 자동 판단하되, `mode=` 로 강제 지정 가능하게 한다.
**`socket` 모드에서 `@app.action` 을 등록하면 기동 시 경고를 낸다** (조용히 무시하지 않는다).

### 2.4 결정 D4 — `ts` 는 Mattermost post id 를 그대로 담는다

Slack `ts`("1754620800.123456")와 MM `post id`(26자 문자열)는 형식이 다르다.

- **기본값**: `ts = post_id` (문자열 불투명값으로 취급하는 대부분의 코드가 그대로 동작)
- `thread_ts` ↔ `root_id` 매핑
- **리스크**: `float(ts)` 로 시간 비교/정렬하는 앱 코드는 깨진다 →
  `App(ts_format="epoch")` 옵션 제공 (`create_at` ms → Slack ts 포맷 변환 + 역매핑 캐시 유지)
- 마이그레이션 가이드에 "ts 산술 연산 여부"를 사전 점검 항목으로 명시

### 2.5 결정 D5 — Block Kit 은 "손실 허용 변환기"로 처리한다

Mattermost는 Slack 호환 **Message Attachments** 를 지원하고, **Interactive Dialog** 가 Modal에 대응된다.
Block Kit 전체를 커버할 수 없으므로 변환기는 **지원 블록만 변환하고 나머지는 텍스트 폴백 + 경고 로그**를 남긴다.

| Block Kit | Mattermost 대응 | v1 |
|---|---|---|
| `section` (text) | attachment `text` | ✅ |
| `section` + `fields` | attachment `fields` | ✅ |
| `divider` | `---` 마크다운 | ✅ |
| `context` | attachment `footer` | ✅ |
| `image` | attachment `image_url` | ✅ |
| `header` | `#### 텍스트` | ✅ |
| `actions`(button) | attachment `actions[type=button]` | ✅ |
| `actions`(static_select) | attachment `actions[type=select]` | ✅ |
| `input` (모달 내부) | dialog `elements` | ✅ |
| `overflow`, `datepicker`, `rich_text` | 없음 | ⚠️ 폴백 |

### 2.6 결정 D6 — `slack_bolt` 이름 그대로 쓰는 shim 을 별도 패키지로 낸다

핸들러 코드 무수정(G1)을 극대화하기 위해, import 문조차 건드리지 않는 경로를 제공한다.

```python
# 방법 A (권장) — 명시적
from mattermost_bolt import App

# 방법 B — 무수정 마이그레이션
import mattermost_bolt.compat.install  # sys.modules 에 slack_bolt 등록
from slack_bolt import App  # 실제로는 mattermost_bolt.App
```

방법 B는 편의 기능이며, 진단이 어려워지므로 문서에 **"검증용/과도기용"** 임을 명기한다.

---

## 3. API 매핑 명세

### 3.1 데코레이터

| Slack Bolt | Mattermost 소스 | 비고 |
|---|---|---|
| `@app.event("message")` | WS `posted` | |
| `@app.event("reaction_added")` | WS `reaction_added` | |
| `@app.event("member_joined_channel")` | WS `user_added` | |
| `@app.event("channel_created")` | WS `channel_created` | |
| `@app.message("hello" \| re.compile(...))` | WS `posted` + 본문 매칭 | |
| `@app.command("/status")` | HTTP `/mmbolt/commands` 또는 WS 텍스트 파싱 | 모드 의존 |
| `@app.action("button_id")` | HTTP `/mmbolt/actions` | attachment action `context.action_id` |
| `@app.view("callback_id")` | HTTP `/mmbolt/dialogs` | dialog submission |
| `@app.shortcut(...)` | 대응 없음 | 전용 slash command 로 폴백 + 경고 |
| `@app.options(...)` | dialog `data_source` / select options | v2 |
| `@app.use(mw)` | 공통 미들웨어 체인 | 그대로 |
| `@app.error(fn)` | 공통 에러 훅 | 그대로 |

### 3.2 핸들러 인자 주입

| 인자 | Mattermost 구현 |
|---|---|
| `say(text, blocks, thread_ts)` | `POST /api/v4/posts` (이벤트 발생 채널로 고정) |
| `ack(response=None)` | WS 경로: no-op / HTTP 경로: 즉시 응답 본문 확정 |
| `respond(text, response_type)` | `response_url` 로 POST (MM도 동일 개념 지원) |
| `client` | `WebClient` 호환 파사드 (§3.3) |
| `body`, `payload`, `event` | 정규화된 Slack 형태 dict |
| `context` | `BoltContext` (`user_id`, `channel_id`, `team_id`, `bot_token`, `matches`) |
| `logger` | 표준 `logging.Logger` |
| `next()` | 미들웨어 체인 진행 |

### 3.3 WebClient 메서드 매핑 (v1 범위)

| Slack WebClient | Mattermost REST v4 |
|---|---|
| `auth_test()` | `GET /users/me` |
| `chat_postMessage()` | `POST /posts` |
| `chat_postEphemeral()` | `POST /posts/ephemeral` |
| `chat_update()` | `PUT /posts/{id}` |
| `chat_delete()` | `DELETE /posts/{id}` |
| `conversations_list()` | `GET /teams/{team_id}/channels` |
| `conversations_open()` | `POST /channels/direct` |
| `conversations_history()` | `GET /channels/{id}/posts` |
| `conversations_members()` | `GET /channels/{id}/members` |
| `users_info()` / `users_list()` | `GET /users/{id}` / `GET /users` |
| `reactions_add()` / `reactions_remove()` | `POST /reactions` / `DELETE /users/{u}/posts/{p}/reactions/{emoji}` |
| `files_upload_v2()` | `POST /files` → `POST /posts` (`file_ids`) |
| `views_open()` | `POST /actions/dialogs/open` (`trigger_id` 필요) |
| `views_update()` / `views_push()` | 대응 없음 → `NotImplementedError` + 대안 안내 |

반환값은 `SlackResponse` 호환 객체(`resp["ts"]`, `resp["channel"]`, `resp["ok"]`, `.get()`, `.data`).

### 3.4 Slash Command 페이로드 — 거의 무변환

Mattermost slash command POST 필드가 Slack과 사실상 동일하다. **v1에서 가장 저비용·고효과 구간이다.**

```
공통 : token, team_id, team_domain, channel_id, channel_name,
       user_id, user_name, command, text, response_url, trigger_id
보정 : api_app_id (MM 없음 → 고정값 주입), enterprise_id (None)
```

### 3.5 파일 트리 (예정)

```
mattermost-bolt/
├── pyproject.toml
├── README.md
├── src/mattermost_bolt/
│   ├── __init__.py              App, AsyncApp, BoltContext 재노출
│   ├── app.py                   리스너 등록 / 미들웨어 체인 / dispatch
│   ├── context.py
│   ├── listener/
│   │   ├── matcher.py           message/event/command/action/view 매처
│   │   └── args.py              인자 주입 (say/ack/respond/client/...)
│   ├── adapter/
│   │   ├── socket_mode.py       SocketModeHandler 호환 진입점
│   │   ├── ws_client.py         MM WebSocket (인증·seq·재연결·하트비트)
│   │   └── http_receiver.py     command/action/dialog 수신 (Flask/직접 asyncio)
│   ├── web/
│   │   ├── client.py            WebClient 호환 파사드
│   │   └── response.py          SlackResponse 호환
│   ├── payload/
│   │   ├── event.py             MM posted → Slack event 정규화
│   │   ├── command.py
│   │   └── action.py            attachment action / dialog → block_actions·view_submission
│   ├── blocks/
│   │   └── kit.py               Block Kit → attachments / dialog 변환기
│   └── compat/
│       ├── shim.py
│       └── install.py
├── tests/
├── examples/
│   ├── 01_hello_message.py
│   ├── 02_slash_command.py
│   ├── 03_interactive_button.py
│   └── 04_modal_dialog.py
├── spikes/
└── dev/
    ├── setup.sh                 팀/채널/봇/슬래시커맨드 생성 (재실행 안전)
    ├── app.sh                   앱 프로세스 관리 (start/stop/status/log)
    └── verify.sh                환경 점검
```

---

## 4. 개발 환경 (대상 서버)

### 4.1 서버 사용 방침

기존 `channel-bridge` 개발 환경과 **충돌하지 않도록 리소스를 분리**한다.

| 항목 | 값 | 근거 |
|---|---|---|
| 인스턴스 | **MM-B `http://192.168.0.136:8072`** | mm-a는 브릿지 개발이 상시 사용 |
| 팀 | 신규 `bolt` ("Bolt Test") | 기존 `bridge` 팀 픽스처 보존 |
| 채널 | `bolt-dev` (공개) | |
| 봇 | 신규 `boltbot` ("Mattermost Bolt") | `bridgebot` 토큰 재사용 금지 |
| 앱 HTTP 포트 | **8099** (대안 8065, 8073, 3030) | 2026-08-08 `ss -ltn` 실측 결과 모두 미사용 |
| 프로젝트 경로 | `/home/nextlab-3050/dev/mattermost-bolt/` | |
| 토큰 보관 | `dev/.tokens.env` (chmod 600, .gitignore) | 기존 프로젝트 관례 준수 |

> **주의** — 기존 compose 프로젝트(`chbridge-dev`)에 `docker compose down -v` 를 실행하면
> MM-B의 `bolt` 팀·채널·봇이 함께 삭제된다. `dev/setup.sh` 를 **재실행 안전(idempotent)** 하게 만들어 즉시 복구 가능하게 한다.
>
> 브릿지 개발과의 간섭이 문제가 되면 **대안 B**: `mmbolt-dev` compose 프로젝트로 MM 1대(포트 8073) + DB를 별도 기동.
> 서버 여유(36코어/125GB)가 충분하므로 비용은 낮다. Phase 0에서 간섭 여부를 보고 판단한다.

### 4.2 컨테이너 → 호스트 앱 도달 경로 (중요)

MM은 컨테이너 안에서 돌고, 앱은 호스트에서 돈다. slash command / interactive의 `request_url` 은
**컨테이너 기준으로 해석**되므로 `localhost:8099` 는 동작하지 않는다.

- 등록 URL: `http://192.168.0.136:8099/mmbolt/...` (호스트 LAN IP 사용)
- 또는 compose에 `extra_hosts: ["host.docker.internal:host-gateway"]` 추가 후 `http://host.docker.internal:8099/...`
- **Phase 0의 첫 스파이크로 이 경로를 반드시 실측한다** — 여기서 막히면 이후 전 단계가 지연된다.

### 4.3 사전 확인 사항

```bash
ssh Ubuntu-3050
export PATH="$HOME/.local/bin:$PATH"

# 1) 포트 여유 확인
ss -ltn | grep -E '8099|8065|8073|3030'      # 출력 없으면 사용 가능

# 2) MM 에디션·버전 (Team Edition 유지 확인)
curl -s http://192.168.0.136:8072/api/v4/system/ping?get_server_status=true

# 3) 필요한 서버 설정 (이미 적용되어 있음 — 재확인용)
cd /home/nextlab-3050/dev/channel-bridge/dev
docker compose exec mm-b mmctl --local config get ServiceSettings.EnableCommands
docker compose exec mm-b mmctl --local config get ServiceSettings.EnablePostUsernameOverride
```

`EnableCommands` 가 꺼져 있으면 slash command 등록이 불가하다 → compose 환경변수
`MM_SERVICESETTINGS_ENABLECOMMANDS=true` 추가 후 재기동이 필요하며, 이는 **브릿지 인스턴스 재시작을 수반**하므로
사전에 합의한다.

---

## 5. 단계별 실행 계획

전체 **6주(30 영업일)** 기준. 각 Phase 종료 시 데모 가능한 산출물이 나오도록 구성했다.

### Phase 0 — 기술 검증 스파이크 (3일)

가장 불확실한 것부터 깨뜨린다. 코드 품질은 신경쓰지 않고 `spikes/` 에 던진다.

| # | 스파이크 | 통과 기준 |
|---|---|---|
| S1 | MM WebSocket 연결 → `posted` 이벤트 수신 | 봇 토큰으로 인증, 채널 메시지가 콘솔에 찍힘 |
| S2 | slash command 등록 → 호스트 앱이 POST 수신 | §4.2 도달 경로 확정 |
| S3 | attachment 버튼 게시 → 클릭 시 앱이 POST 수신 | `context` 왕복 확인 |
| S4 | `trigger_id` 로 dialog 오픈 → submission 수신 | Modal 대응 가능성 확정 |
| S5 | Team Edition에서 S1~S4 전부 동작 | 유료 기능 의존 0 확인 |

**산출물**: `spikes/*.py`, 각 페이로드 실물 JSON 덤프 (`spikes/payloads/`)
→ 이 덤프가 Phase 1~3 정규화 로직과 테스트 픽스처의 **단일 진실 공급원**이 된다.

**게이트**: S4 실패 시 `@app.view` 를 v1 범위에서 제외하고 Phase 3을 축소 재계획한다.

---

### Phase 1 — 코어 런타임 (7일)

| 작업 | 상세 |
|---|---|
| 프로젝트 스캐폴딩 | `uv init`, pyproject, ruff/mypy/pytest 설정 |
| `ws_client.py` | 인증 챌린지, seq 관리, 하트비트, **지수 백오프 재연결** |
| `app.py` | 리스너 레지스트리, 미들웨어 체인, dispatch 루프 |
| `payload/event.py` | MM `posted`(→ `data.post` JSON 문자열 파싱) → Slack event 정규화 |
| `listener/args.py` | `say` / `client` / `body` / `event` / `context` / `logger` 주입 |
| `web/client.py` | `chat_postMessage`, `chat_update`, `chat_delete`, `users_info`, `auth_test` |
| 자기 루프 방지 | `ignore_self=True` 기본. 봇 자신의 post 무시 |
| `SocketModeHandler` | `SocketModeHandler(app, app_token=...).start()` 시그니처 호환 |

**데모**: `examples/01_hello_message.py` — `@app.message("hello")` 로 `bolt-dev` 채널에서 응답

```python
from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=MM_BOT_TOKEN, server_url="http://192.168.0.136:8072")


@app.message("hello")
def handle_hello(message, say):
    say(f"Hey there <@{message['user']}>!")


SocketModeHandler(app).start()
```

**게이트 G-P1**: 위 코드가 Slack Bolt 공식 예제와 **문자 단위로 동일한 핸들러 본문**을 갖는다.

---

### Phase 2 — Slash Command + HTTP 리시버 (5일)

| 작업 | 상세 |
|---|---|
| `http_receiver.py` | `/mmbolt/commands`, `/mmbolt/actions`, `/mmbolt/dialogs` 라우트 |
| `payload/command.py` | MM → Slack command 페이로드 (§3.4) |
| `ack()` 의미론 | HTTP 경로에서 3초 내 응답. `ack("text")` → `{"response_type":"ephemeral","text":...}` |
| `respond()` | `response_url` POST, `replace_original` / `delete_original` 지원 |
| 토큰 검증 | MM slash command token 검증 (Slack `signing_secret` 자리 대체) |
| Pseudo Socket Mode | WS 텍스트에서 `/cmd args` 파싱 → 동일 리스너로 라우팅 (D3) |
| `dev/setup.sh` | 팀·채널·봇·슬래시커맨드 자동 등록 (mmctl LocalMode 활용) |

**데모**: `examples/02_slash_command.py` — `/status` 입력 시 ephemeral 응답

**게이트 G-P2**: 동일 `@app.command("/status")` 핸들러가 `socket` 모드와 `http` 모드 **양쪽에서 동작**한다.

---

### Phase 3 — 인터랙티브 + Block Kit 변환 (8일)

| 작업 | 상세 |
|---|---|
| `blocks/kit.py` | Block Kit → attachments 변환 (§2.5 표) |
| `payload/action.py` | attachment action → Slack `block_actions` 정규화 |
| `@app.action` | `action_id` 를 `context` 에 실어 왕복. 문자열/정규식/dict 매처 지원 |
| `views_open()` | Block Kit modal → MM Interactive Dialog 변환 |
| `@app.view` | dialog submission → Slack `view_submission` 정규화 |
| 검증 오류 응답 | `ack(response_action="errors")` → dialog `errors` 필드 |
| 미지원 폴백 | 변환 불가 블록은 텍스트 폴백 + `logger.warning` (**조용한 실패 금지**) |

**데모**: `examples/03_interactive_button.py`, `examples/04_modal_dialog.py`

**게이트 G-P3**: Slack Block Kit Builder에서 만든 JSON을 그대로 넣어 Mattermost에 렌더된다.

---

### Phase 4 — 마이그레이션 검증 + 안정화 (5일)

여기가 **프로젝트의 진짜 시험대**다. 새 코드를 짜는 게 아니라 **실제 앱을 옮긴다.**

| 작업 | 상세 |
|---|---|
| 검증 대상 선정 | 사내 운영 중인 Slack Bolt 앱 **2종** (단순형 1 + 인터랙티브 포함 1) |
| 마이그레이션 실행 | import·초기화만 수정. **diff 라인 수를 기록** (G1 측정치) |
| 갭 리포트 | 막힌 지점 전부 이슈화 → v1 수정 / v2 백로그 분류 |
| 재연결·크래시 테스트 | 프로세스 kill 후 재기동, WS 끊김 중 발생 이벤트 처리 방침 확정 |
| `compat/shim` | `slack_bolt` 이름 가로채기 (D6) |
| 부하 확인 | 초당 이벤트 처리량 측정, 백프레셔 방침 결정 |

**게이트 G-P4**: 검증 앱 2종이 `bolt-dev` 채널에서 정상 동작하고, 핸들러 본문 diff가 0줄이다.

---

### Phase 5 — 문서화 / 패키징 / 공개 (5일)

| 작업 | 상세 |
|---|---|
| `README.md` | 5분 Quickstart, Slack↔MM 대조표 |
| `MIGRATION.md` | 마이그레이션 체크리스트, 미지원 목록, `ts` 산술 주의(D4) |
| API 레퍼런스 | 데코레이터·인자·WebClient 매핑 전표 |
| 패키징 | 내부 registry(`192.168.0.136:33999`) 또는 PyPI 배포 |
| CI | ruff / mypy / pytest 게이트 |
| 오픈소스화 판단 | 라이선스(MIT), 사내 정보 제거 확인 후 공개 여부 결정 |

---

## 6. 일정 요약

```
주차   1        2        3        4        5        6
     ┌────────┬────────┬────────┬────────┬────────┬────────┐
P0   │███     │        │        │        │        │        │  스파이크 (3d)
P1   │   █████│██      │        │        │        │        │  코어 런타임 (7d)
P2   │        │  ██████│        │        │        │        │  Command/HTTP (5d)
P3   │        │        │████████│████    │        │        │  인터랙티브 (8d)
P4   │        │        │        │    ████│█       │        │  마이그레이션 검증 (5d)
P5   │        │        │        │        │ ███████│█       │  문서/패키징 (5d)
     └────────┴────────┴────────┴────────┴────────┴────────┘
        ▲G-P0    ▲G-P1    ▲G-P2   ▲G-P3     ▲G-P4    ▲Release
```

**최단 경로 옵션(MVP 2주)**: Phase 0 + 1 + 2 만 수행하면 `message` / `event` / `command` 를 지원하는
실사용 가능한 라이브러리가 나온다. 인터랙티브 요구가 없는 앱은 이 시점에 마이그레이션 가능하다.
아이디어 문서의 "PoC부터 시작" 권고에 부합하므로, **2주 시점을 1차 의사결정 지점으로 삼는다.**

---

## 7. 리스크 관리

| ID | 리스크 | 영향 | 대응 |
|---|---|---|---|
| R1 | `trigger_id` 로 dialog 오픈이 TE에서 제약 | Modal 미지원 | Phase 0 S4에서 조기 확인. 실패 시 임시 채널 포스트 폼으로 폴백 |
| R2 | 컨테이너→호스트 HTTP 도달 실패 | command/action 전면 차단 | Phase 0 S2. `host.docker.internal` 대안 준비 |
| R3 | `ts` 형식 차이로 앱 코드 파손 | 마이그레이션 실패 | D4의 `ts_format="epoch"` 옵션. MIGRATION.md 체크리스트 |
| R4 | Block Kit 변환 손실 | UI 저하 | 폴백 + 경고. 지원 블록 목록을 문서에 명시 |
| R5 | 봇이 채널에 없어 이벤트 미수신 | 무응답 | 기동 시 봇 채널 소속 점검 + 경고 로그 |
| R6 | 브릿지 개발 환경과 간섭 | 양쪽 개발 지연 | §4.1 리소스 분리. 심화 시 대안 B(별도 compose) |
| R7 | WS 끊김 중 이벤트 유실 | 메시지 누락 | 재연결 후 `GET /channels/{id}/posts?since=` 로 갭 보충(v1은 옵션) |
| R8 | MM API 버전 변경 | 파손 | 대상 버전(11.10.0) 고정 명시 + CI에서 실서버 스모크 |

**최대 리스크는 R2·R1이며 둘 다 Phase 0에서 3일 안에 판정된다.** 이것이 스파이크를 최우선에 둔 이유다.

---

## 8. 테스트 전략

| 층위 | 대상 | 도구 |
|---|---|---|
| 단위 | 페이로드 정규화, Block Kit 변환, 매처 | pytest + Phase 0 실물 JSON 픽스처 |
| 계약 | WebClient ↔ MM REST 응답 스키마 | pytest + `respx`(httpx mock) |
| 통합 | 실제 MM-B 인스턴스 대상 왕복 | `spikes/e2e_*.py` (기존 channel-bridge 관례 준용) |
| 회귀 | 검증 앱 2종 시나리오 | 스크립트 자동화 |
| 복구 | kill → 재기동 → 유실 0 | `spikes/e2e_crash.sh` 패턴 재사용 |

품질 게이트는 기존 프로젝트와 동일하게 `uv run ruff check .` / `uv run ruff format .` / `uv run mypy` / `uv run pytest -q`.

---

## 9. 착수 즉시 실행할 작업 (Day 1)

```bash
ssh Ubuntu-3050
export PATH="$HOME/.local/bin:$PATH"
mkdir -p ~/dev/mattermost-bolt && cd ~/dev/mattermost-bolt
uv init --python 3.13
uv add websockets httpx
mkdir -p spikes/payloads src/mattermost_bolt dev tests examples
```

1. MM-B에 `bolt` 팀 / `bolt-dev` 채널 / `boltbot` 봇 생성 후 토큰을 `dev/.tokens.env` (chmod 600) 에 저장
2. `spikes/s1_ws.py` 작성 → `posted` 이벤트 수신 확인
3. `spikes/s2_command.py` 작성 → §4.2 도달 경로 판정
4. 수신 페이로드 전부 `spikes/payloads/*.json` 으로 덤프

---

## 10. 자격증명 취급 원칙

- SSH 비밀번호, 봇 토큰, DB 비밀번호는 **이 문서를 포함한 어떤 git 추적 파일에도 기록하지 않는다**
- 보관 위치는 `dev/.tokens.env`, `dev/.env` (chmod 600, `.gitignore` 등재)로 한정한다
- 서버 접속 정보는 `우분투서버_SSH&Mattermost설치정보.txt` 를 참조하며, 이 파일 역시 커밋 대상이 아니다
- 오픈소스 공개(Phase 5) 전 `git log -p` 전수 검사로 자격증명 혼입 여부를 확인한다

---

## 부록 A. Slack Bolt ↔ Mattermost Bolt 초기화 비교

```python
# ── Slack Bolt ──────────────────────────────────────
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])
SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()

# ── Mattermost Bolt ─────────────────────────────────
from mattermost_bolt import App
from mattermost_bolt.adapter.socket_mode import SocketModeHandler

app = App(
    token=os.environ["MM_BOT_TOKEN"],
    server_url=os.environ["MM_SERVER_URL"],  # 추가되는 유일한 필수 인자
    team=os.environ.get("MM_TEAM"),  # 선택
)
SocketModeHandler(app).start()
```

**변경점은 초기화 3줄뿐이며, 이 아래의 모든 핸들러 코드는 동일하다.** 이것이 본 프로젝트의 계약이다.

## 부록 B. 참고 자료

- Mattermost API v4 Reference — `https://api.mattermost.com/`
- Mattermost WebSocket Events — `https://developers.mattermost.com/integrate/websocket/`
- Mattermost Interactive Messages / Dialogs — `https://developers.mattermost.com/integrate/plugins/interactive-messages/`
- Slack Bolt for Python — `https://tools.slack.dev/bolt-python/`
- 대상 서버 상세 — `우분투서버_SSH&Mattermost설치정보.txt`
