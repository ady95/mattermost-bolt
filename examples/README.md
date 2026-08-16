# 예제 안내와 테스트 방법

mattermost-bolt 의 기능을 하나씩 보여주는 예제 01~10 과, 이를 실제
Mattermost 서버에서 검증하는 절차입니다. 공통 준비를 먼저 마친 뒤,
예제별 시나리오를 따라 하세요.

| # | 파일 | 내용 |
|---|---|---|
| 01 | [01_hello_message.py](01_hello_message.py) | 메시지 리스닝 — Slack Bolt 와 동일한 핸들러 |
| 02 | [02_slash_command.py](02_slash_command.py) | 슬래시 명령 (socket / http 두 모드) |
| 03 | [03_interactive.py](03_interactive.py) | Block Kit 버튼과 모달(다이얼로그) |
| 04 | [04_migrated_slack_app.py](04_migrated_slack_app.py) | 실제 Slack 앱을 무수정 마이그레이션한 모습 |
| 05 | [05_reaction_workflow.py](05_reaction_workflow.py) | 리액션 트리거 — ✅ 승인 처리, 📌 DM 북마크 |
| 06 | [06_thread_mention_bot.py](06_thread_mention_bot.py) | 멘션 감지(`app_mention` 대체)와 스레드 요약 |
| 07 | [07_poll_bot.py](07_poll_bot.py) | 투표 봇 — 클릭마다 메시지를 다시 그리는 상태형 인터랙션 |
| 08 | [08_report_upload.py](08_report_upload.py) | 채널 활동 집계 CSV 를 `files_upload_v2` 로 업로드 |
| 09 | [09_onboarding_bot.py](09_onboarding_bot.py) | 채널 온보딩 — 환영 인사·DM 안내·새 채널 자동 합류 |
| 10 | [10_middleware_compat.py](10_middleware_compat.py) | 미들웨어·에러 핸들러와 `compat.install` 무수정 실행 |

---

## 1. 공통 준비

### 1.1 봇 계정 만들기

1. System Console → Integrations → Bot Accounts → **Enable Bot Account Creation** 을 켠다.
2. Integrations → Bot Accounts → **Add Bot Account** 로 봇을 만들고 **Access Token** 을 복사한다.
3. 봇을 **테스트할 팀과 채널에 초대**한다. 봇은 자신이 속한 채널의 이벤트만 받는다.
   - 팀 초대: 팀 메뉴 → Invite People → 봇 계정 추가
   - 채널 초대: 채널에서 `/invite @봇이름`

### 1.2 환경변수

```bash
export MM_BOT_TOKEN="봇 액세스 토큰"
export MM_SERVER_URL="https://mattermost.example.com"
export MM_TEAM="팀이름"          # 선택 — 채널 이름 해석에 사용 (권장)
```

Windows PowerShell:

```powershell
$env:MM_BOT_TOKEN = "봇 액세스 토큰"
$env:MM_SERVER_URL = "https://mattermost.example.com"
$env:MM_TEAM = "팀이름"
```

### 1.3 의존성 설치와 연결 확인

```bash
uv pip install -e ".[dev]"

# 토큰·서버 연결이 정상인지 10초 안에 확인
python -c "
import os
from mattermost_bolt import App
app = App(token=os.environ['MM_BOT_TOKEN'], server_url=os.environ['MM_SERVER_URL'])
print(app.client.auth_test().data)
"
```

`{'ok': True, 'user': '봇이름', ...}` 이 나오면 준비 완료입니다.

### 1.4 두 가지 모드 — 어떤 예제에 무엇이 필요한가

| 예제 | 모드 | 인바운드 포트 | Mattermost 슬래시 명령 등록 |
|---|---|---|---|
| 01, 04, 05, 06, 09, 10 | socket | 불필요 | 불필요 |
| 02, 08 | socket / http 겸용 | http 일 때만 | http 일 때만 |
| 03, 07 | **http 필수** | 필요 (기본 8099) | 필요 |

**http 모드 추가 준비** (03, 07 및 http 로 돌리는 02, 08):

1. 앱이 실제로 도달되는 주소를 지정한다.

   ```bash
   export MM_REQUEST_URL="http://10.0.0.5:8099"   # localhost 금지 (아래 참고)
   ```

2. Mattermost 가 Docker 안에 있다면 `MM_REQUEST_URL` 에 `localhost` 를 쓰면
   안 된다(컨테이너 자신을 가리킨다). 호스트 LAN IP 또는
   `host.docker.internal` 을 쓴다.
3. 사설 IP 는 기본 차단이므로 허용 목록에 추가한다.

   ```bash
   mmctl config set ServiceSettings.AllowedUntrustedInternalConnections "10.0.0.5"
   ```

   (System Console → Environment → Developer → *Allow untrusted internal connections to*)

4. 슬래시 명령을 등록한다: Integrations → Slash Commands → Add Slash Command
   - Request URL: `{MM_REQUEST_URL}/mmbolt/commands`
   - Request Method: `POST`
   - 등록할 트리거: `approve`, `feedback`(03) / `poll`(07) / 필요 시 `status`, `echo`, `slow`(02), `report`(08), `admin`(10)

> socket 모드의 슬래시 명령은 WebSocket 으로 받은 `/명령` 텍스트를 파싱하므로
> 등록이 필요 없지만, `trigger_id` 가 없어 **모달(다이얼로그)은 열 수 없습니다.**

---

## 2. 예제별 테스트 시나리오

각 예제는 `python examples/파일명.py` 로 실행합니다. 종료는 `Ctrl+C`.

### 01 — 메시지 리스닝 (`01_hello_message.py`)

| 하는 일 | 기대 결과 |
|---|---|
| 봇이 있는 채널에 `hello` 입력 | `Hey there @나!` 응답 |
| `deploy staging` 입력 | `staging 배포를 시작합니다.` 응답 |
| `thread` 입력 | 스레드 답글이 달림 |
| 아무 메시지에 리액션 추가 | 콘솔 로그에 리액션 기록 |

### 02 — 슬래시 명령 (`02_slash_command.py`)

```bash
MM_BOLT_MODE=socket python examples/02_slash_command.py   # 등록 없이 바로
```

| 하는 일 | 기대 결과 |
|---|---|
| `/status` 입력 | `Status: OK` (본인에게만 보임) |
| `/echo 안녕` 입력 | `입력값: 안녕` |
| `/slow` 입력 | 즉시 응답 후 완료 메시지가 이어짐 |

http 모드로도 검증하려면 §1.4 준비 후 `MM_BOLT_MODE=http MM_REQUEST_URL=... ` 로 재실행합니다.

### 03 — 버튼과 모달 (`03_interactive.py`) — http 필수

| 하는 일 | 기대 결과 |
|---|---|
| `/approve 서버 증설` 입력 | 승인/반려 버튼이 달린 메시지 게시 |
| **승인** 클릭 | 메시지가 `✅ 서버 증설 승인 — @나` 로 교체됨 |
| `/feedback` 입력 | 다이얼로그(모달)가 열림 |
| 내용을 4자 이하로 제출 | `5자 이상 입력하세요.` 검증 오류 표시 |
| 정상 제출 | 채널에 피드백 메시지 게시 |
| 다이얼로그 취소 | 콘솔에 취소 로그 |

버튼이 **아예 안 보이면** `MM_REQUEST_URL` 미지정, **눌러도 무반응이면**
Mattermost → 앱 도달 실패입니다(§1.4의 2·3번 확인).

### 05 — 리액션 워크플로 (`05_reaction_workflow.py`)

| 하는 일 | 기대 결과 |
|---|---|
| 아무 메시지에 ✅ (`:white_check_mark:`) 리액션 | 스레드에 `@나 님이 이 메시지를 승인했습니다. ✅` + 봇이 🤖 리액션 추가 |
| 아무 메시지에 📌 (`:pushpin:`) 리액션 | 봇 DM 으로 해당 메시지 permalink 도착 |
| 다른 이모지 리액션 | 콘솔 로그만 남고 무반응 |
| 리액션 제거 | 콘솔 로그 |

### 06 — 스레드 멘션 봇 (`06_thread_mention_bot.py`)

```bash
export MM_BOT_NAME=boltbot   # 실제 봇 username 과 일치해야 한다
```

| 하는 일 | 기대 결과 |
|---|---|
| 채널에서 `@boltbot 안녕` 입력 | 스레드로 안내 응답 |
| 그 스레드에서 몇 명이 대화 후 `요약` 입력 | 시작 메시지·메시지 수·참여자 목록 요약이 스레드로 도착 |
| 스레드 밖에서 `요약` 입력 | "스레드 안에서만 요약할 수 있습니다" 안내 |

### 07 — 투표 봇 (`07_poll_bot.py`) — http 필수

| 하는 일 | 기대 결과 |
|---|---|
| `/poll 점심 뭐 먹을까요?; 짜장면; 짬뽕; 볶음밥` | 옵션 버튼 3개 + 마감 버튼이 달린 투표 게시 |
| `/poll 배포 진행할까요?` (옵션 생략) | 👍/👎 두 옵션으로 생성 |
| 옵션 버튼 클릭 | 메시지의 집계(표 수·막대)가 즉시 갱신 |
| 같은 사람이 다른 옵션 클릭 | 표가 **이동**함 (총 참여자 수 불변) |
| 작성자가 아닌 사람이 **마감** 클릭 | "투표를 만든 사람만 마감할 수 있습니다" (본인에게만 보임) |
| 작성자가 **마감** 클릭 | 버튼이 사라지고 `(마감)` 표시로 교체 |
| 앱 재시작 후 기존 투표 버튼 클릭 | "이미 종료되었거나 알 수 없는 투표입니다" (메모리 상태라서 정상) |

### 08 — 리포트 업로드 (`08_report_upload.py`)

| 하는 일 | 기대 결과 |
|---|---|
| 대화 기록이 있는 채널에서 `/report` | "생성 중" 안내 → `channel_report.csv` 첨부 메시지 게시 → 본인에게만 완료 알림 |
| CSV 를 열어 확인 | `username, real_name, message_count` 열, 메시지 수 내림차순 |
| 새(빈) 채널에서 `/report` | "집계할 메시지가 없습니다" |

### 09 — 온보딩 봇 (`09_onboarding_bot.py`)

다른 테스트 계정이 하나 필요합니다.

| 하는 일 | 기대 결과 |
|---|---|
| 테스트 계정을 봇이 있는 채널에 초대 | 채널에 환영 메시지 + 그 계정의 DM 으로 안내 도착 |
| 테스트 계정이 채널에서 나감 | 콘솔 로그 |
| 새 **공개** 채널 생성 | 봇이 스스로 합류해 축하 메시지 게시 |
| 새 **비공개** 채널 생성 | 봇은 합류하지 못하고 콘솔 로그만 남음 (정상) |

### 10 — 미들웨어·compat (`10_middleware_compat.py`)

| 하는 일 | 기대 결과 |
|---|---|
| 실행 직후 콘솔 확인 | `slack_bolt / slack_sdk import 를 mattermost-bolt 로 대체했습니다` 로그 — compat shim 동작 증거 |
| `핑` 입력 | `퐁 🏓` + 콘솔에 `[message] 처리 N.Nms` 타이밍 로그 |
| `에러` 입력 | 채널 무반응, 콘솔에 `처리 실패: 의도된 실패...` 스택트레이스 — 전역 에러 핸들러 동작 |
| 일반 계정으로 `/admin` | `⛔ 관리자만 사용할 수 있는 명령입니다.` |
| System Admin 계정으로 `/admin test` | `관리자 명령 실행: test` |

> `/admin` 판정은 Mattermost 의 `system_admin` 롤 기준입니다.

### 04 — 마이그레이션 검증 (`04_migrated_slack_app.py`)

01~03 을 통과했다면 04 는 `도움말`, `상태` 입력과 👀 리액션으로 같은 방식으로 확인합니다.

---

## 3. 자동화된 검증 (서버 없이)

실서버 없이도 아래로 회귀 확인이 가능합니다.

```bash
uv run pytest              # 단위 테스트
uv run ruff check .        # 린트 (examples/ 포함)
uv run ruff format --check .

# 예제 파일이 임포트 수준에서 깨지지 않는지 (더미 환경변수로 실행)
MM_BOT_TOKEN=x MM_SERVER_URL=http://localhost MM_REQUEST_URL=http://localhost:8099 \
python -c "
import runpy
for f in ['01_hello_message','02_slash_command','03_interactive','04_migrated_slack_app',
          '05_reaction_workflow','06_thread_mention_bot','07_poll_bot',
          '08_report_upload','09_onboarding_bot','10_middleware_compat']:
    runpy.run_path('examples/' + f + '.py', run_name='not_main'); print(f, 'OK')
"
```

실서버 스모크 테스트:

```bash
export MM_TEST_CHANNEL=bolt-dev
uv run python spikes/e2e_smoke.py
```

---

## 4. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 메시지에 반응이 없다 | 봇이 채널에 없음 | `/invite @봇이름` |
| 슬래시 명령이 `... failed.` | 컨테이너 → 앱 도달 실패 | §1.4의 2·3번 |
| 버튼을 눌러도 무반응 | socket 모드로 실행함 | `mode="http"` + `MM_REQUEST_URL` |
| 버튼이 아예 안 보인다 | `MM_REQUEST_URL` 미지정 | 환경변수 지정 후 재실행 |
| 모달이 안 열린다 | socket 모드 (trigger_id 없음) | http 모드 + 슬래시 명령 등록 |
| 06 이 멘션에 무반응 | `MM_BOT_NAME` 이 실제 username 과 다름 | 봇 계정의 username 으로 수정 |
| `MattermostApiError: 403` | 봇 권한 부족 | 봇을 팀·채널에 추가, 토큰 확인 |
| WebSocket 재연결 반복 | 토큰 무효 | §1.3 의 `auth_test` 로 확인 |

더 자세한 진단은 [MIGRATION.md](../MIGRATION.md) §5 를 참고하세요.
