# Slack Bolt → mattermost-bolt 마이그레이션 가이드

이 문서는 실제로 옮길 때 **무엇이 걸리는지**를 순서대로 정리한 것입니다.
가장 자주 문제가 되는 항목을 앞에 두었습니다.

---

## 0. 30초 요약

바꿔야 하는 것은 초기화 세 줄입니다.

```diff
- from slack_bolt import App
- from slack_bolt.adapter.socket_mode import SocketModeHandler
+ from mattermost_bolt import App
+ from mattermost_bolt.adapter.socket_mode import SocketModeHandler

- app = App(token=os.environ["SLACK_BOT_TOKEN"])
+ app = App(
+     token=os.environ["MM_BOT_TOKEN"],
+     server_url=os.environ["MM_SERVER_URL"],
+ )

- SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
+ SocketModeHandler(app).start()
```

그 다음, 아래 체크리스트를 훑어 걸리는 항목이 있는지 확인하세요.

---

## 1. 사전 점검 체크리스트

옮기기 전에 기존 코드에서 아래를 검색해 보세요. **하나라도 걸리면 §2 의 해당 절을 읽으세요.**

| # | 검색어 | 걸리면 볼 곳 |
|---|---|---|
| C1 | `float(` 또는 `sort` 와 함께 쓰인 `ts` | [§2.1 ts 는 산술 대상이 아니다](#21-ts-는-산술-대상이-아니다) |
| C2 | `app.action` / `app.view` / `views_open` | [§2.2 인터랙션은 HTTP 가 필요하다](#22-인터랙션은-http-가-필요하다) |
| C3 | `views_update` / `views_push` / `views_publish` | [§2.3 대응물이 없는 기능](#23-대응물이-없는-기능) |
| C4 | `app.shortcut` | [§2.3 대응물이 없는 기능](#23-대응물이-없는-기능) |
| C5 | `blocks=[...]` 안의 `overflow` / `datepicker` / `rich_text` | [§2.4 Block Kit 손실](#24-block-kit-은-손실-변환된다) |
| C6 | `*bold*` 처럼 Slack 서식을 직접 만든 문자열 | [§2.5 서식 자동 변환](#25-서식은-자동-변환된다) |
| C7 | `channel="C01234567"` 처럼 하드코딩된 채널 ID | [§2.6 식별자](#26-식별자는-형식이-다르다) |
| C8 | `app.event("app_mention")` | [§2.7 이벤트 차이](#27-이벤트-대응표) |
| C9 | `InstallationStore` / `OAuthFlow` / `authorize` | [§2.8 다중 워크스페이스](#28-다중-워크스페이스-설치는-지원하지-않는다) |

---

## 2. 항목별 상세

### 2.1 `ts` 는 산술 대상이 아니다

가장 조용히 깨지는 지점입니다.

| | Slack | Mattermost |
|---|---|---|
| 형식 | `"1754620800.123456"` | `"kb17n49ptj8gzx8at13t5edbia"` (26자) |
| 의미 | 초.마이크로초 | 불투명한 post id |

기본값 `ts_format="post_id"` 에서는 post id 를 `ts` 자리에 그대로 넣습니다.
`ts` 를 **문자열로만** 다루는 코드(전달, 비교, dict 키)는 무수정 동작합니다.

깨지는 패턴:

```python
if float(message["ts"]) > cutoff:          # ValueError
messages.sort(key=lambda m: float(m["ts"]))  # ValueError
datetime.fromtimestamp(float(ts))            # ValueError
```

**해결 A** — 시간이 필요하면 원본 post 를 쓰세요(권장, 정확합니다).

```python
created_ms = message["mattermost_post"]["create_at"]
when = datetime.fromtimestamp(created_ms / 1000)
```

**해결 B** — 코드를 못 고치면 epoch 모드로 전환하세요.

```python
app = App(..., ts_format="epoch")   # ts 가 "1786160418.395000" 형태가 된다
```

epoch 모드는 `ts` ↔ post id 매핑을 메모리에 유지합니다(기본 1만 건 LRU).
캐시에서 밀려난 오래된 `ts` 로 `chat_update` 를 호출하면 실패할 수 있으니,
장기 보관이 필요한 값은 `mattermost_post["id"]` 를 저장하세요.

### 2.2 인터랙션은 HTTP 가 필요하다

Slack 은 Socket Mode 하나로 전부 받지만 Mattermost 는 방향이 다릅니다.

| | 전달 방식 |
|---|---|
| 메시지·이벤트 | Mattermost → 앱 (WebSocket **아웃바운드**) |
| 슬래시 명령·버튼·다이얼로그 | Mattermost → 앱 (HTTP **인바운드**) |

`@app.action` 이나 `@app.view` 를 쓴다면 앱이 HTTP 를 받을 수 있어야 합니다.

```python
app = App(
    token=..., server_url=...,
    mode="http",
    request_url="http://앱이_실제로_도달되는_주소:8099",
)
app.start(port=8099)
```

`socket` 모드로 두면 해당 리스너는 **절대 호출되지 않으며**, 기동 시 경고가 나갑니다.

#### Mattermost 가 Docker 안에 있을 때 (매우 흔한 함정)

1. `request_url` 에 `localhost` 를 쓰면 안 됩니다. 컨테이너 자신을 가리킵니다.
   → 호스트 LAN IP(`http://10.0.0.5:8099`) 또는 `host.docker.internal` 을 쓰세요.
2. Mattermost 는 **사설 IP 로의 아웃바운드 통합 호출을 기본 차단**합니다.
   차단되면 슬래시 명령이 `Command with a trigger of 'x' failed.` 로 실패합니다.

   System Console → Environment → Developer →
   **Allow untrusted internal connections to** 에 호스트를 추가하거나:

   ```bash
   mmctl config set ServiceSettings.AllowedUntrustedInternalConnections "10.0.0.5"
   ```

3. 슬래시 명령은 Mattermost 에 등록해야 합니다
   (Integrations → Slash Commands, Request URL = `{request_url}/mmbolt/commands`).

> 명령만 쓰고 버튼·모달이 없다면 `mode="socket"` 으로 두는 편이 훨씬 간단합니다.
> WebSocket 으로 받은 `/명령` 텍스트를 파싱해 같은 핸들러로 보냅니다.
> 다만 이 경로에는 `trigger_id` 와 `response_url` 이 없어 **모달을 열 수 없습니다**.

### 2.3 대응물이 없는 기능

| Slack | 상태 | 대안 |
|---|---|---|
| `views_update` | ❌ `UnsupportedFeatureError` | 제출 후 새 다이얼로그를 열거나 ephemeral 안내 |
| `views_push` | ❌ | 단일 다이얼로그로 합치기 |
| `views_publish` (Home Tab) | ❌ | 봇 DM 채널에 메시지 게시 |
| `@app.shortcut` | ⚠️ 폴백 | 동명의 슬래시 명령으로 자동 등록 + 경고 |
| `@app.options` (동적 옵션) | ❌ v2 | 정적 옵션 또는 `data_source: users/channels` |
| Workflow Steps | ❌ | — |

`UnsupportedFeatureError` 는 **의도적으로 시끄럽게** 실패합니다.
조용히 성공한 척하면 결함이 운영에서 드러나기 때문입니다.

### 2.4 Block Kit 은 손실 변환된다

Block Kit → Message Attachments / Interactive Dialog 로 변환합니다.

지원: `section`(text/fields/accessory), `header`, `divider`, `context`, `image`,
`actions`(button, static_select, users_select, channels_select), `input`(모달 전용)

미지원(텍스트 폴백 + `logger.warning`): `overflow`, `datepicker`, `timepicker`,
`rich_text`, `video`, `file`

변환 결과를 확인하려면 로그를 `WARNING` 이상으로 켜고 한 번 실행해 보세요.
경고가 없으면 손실이 없다는 뜻입니다.

레이아웃 차이 하나: Mattermost 의 attachment 는 버튼이 항상 하단에 모입니다.
`section` 사이사이에 버튼을 끼운 레이아웃은 **버튼이 나타날 때마다 attachment 를 끊어**
순서를 최대한 보존하지만, Slack 과 픽셀 단위로 같지는 않습니다.

### 2.5 서식은 자동 변환된다

Slack mrkdwn 과 Mattermost 마크다운은 **굵게** 문법이 다릅니다.
자동 변환하지 않으면 모든 메시지 서식이 조용히 어긋납니다.

| Slack | Mattermost | 변환 |
|---|---|---|
| `*굵게*` | `**굵게**` | 자동 |
| `~취소선~` | `~~취소선~~` | 자동 |
| `<url\|라벨>` | `[라벨](url)` | 자동 |
| `<@U123>` | `@U123` | 자동 |
| `<!here>` | `@here` | 자동 |
| `_기울임_` | `_기울임_` | 동일 |

코드 블록/인라인 코드 안은 변환하지 않습니다.
이미 `**` 인 문자열은 이중 변환되지 않습니다.

원문 그대로 보내야 한다면 `App(convert_mrkdwn=False)` 로 끄세요.

### 2.6 식별자는 형식이 다르다

| | Slack | Mattermost |
|---|---|---|
| 채널 | `C01234567` | 26자 영숫자 |
| 사용자 | `U01234567` | 26자 영숫자 |
| 팀 | `T01234567` | 26자 영숫자 |

**하드코딩된 Slack ID 는 전부 교체해야 합니다.** 편의를 위해 이름도 받습니다.

```python
client.chat_postMessage(channel="#general", text="hi")   # 이름으로 조회 후 캐시
client.chat_postMessage(channel="general", text="hi")     # 동일
```

이름 해석에는 팀이 필요합니다. `App(team="myteam")` 을 지정하세요.
지정하지 않으면 봇이 속한 첫 번째 팀을 쓰고 경고를 남깁니다.

### 2.7 이벤트 대응표

| Slack 이벤트 | Mattermost | 비고 |
|---|---|---|
| `message` | `posted` | ✅ |
| `message` (수정) | `post_edited` | ✅ `subtype=message_changed` |
| `message` (삭제) | `post_deleted` | ✅ `subtype=message_deleted` |
| `reaction_added` / `reaction_removed` | 동명 | ✅ |
| `member_joined_channel` | `user_added` | ✅ |
| `member_left_channel` | `user_removed` | ✅ |
| `channel_created` / `channel_deleted` | 동명 | ✅ |
| `user_typing` | `typing` | ✅ |
| **`app_mention`** | **없음** | ⚠️ 아래 참조 |
| `app_home_opened` | 없음 | ❌ |
| `team_join` | 없음 | ❌ |

`app_mention` 은 Mattermost 에 없습니다. 봇 이름 매칭으로 대체하세요.

```python
# 이전: @app.event("app_mention")
@app.message(re.compile(r"@boltbot\b"))
def on_mention(message, say):
    ...
```

봇은 **자신이 속한 채널의 메시지만** 받습니다(Slack 의 이벤트 구독 범위와 유사).
반응이 없다면 먼저 봇이 채널에 초대되어 있는지 확인하세요.

### 2.8 다중 워크스페이스 설치는 지원하지 않는다

`OAuthFlow`, `InstallationStore`, `authorize` 콜백은 v1 범위 밖입니다.
Mattermost 인스턴스 하나 + 봇 토큰 하나 구조를 전제로 합니다.

여러 인스턴스를 다뤄야 한다면 인스턴스마다 `App` 을 만들어 별도 프로세스로 돌리세요.

---

## 3. 자기 메시지 루프 주의

`@app.message` 는 기본적으로 봇 메시지(`subtype=bot_message`)와 자기 자신의
메시지를 건너뜁니다. Slack Bolt 와 동일한 기본값이며 무한 루프 방지 장치입니다.

봇끼리의 대화를 처리해야 한다면 명시적으로 여세요.

```python
@app.event("message")            # message() 대신 event() 를 쓰면 필터가 없다
def all_messages(event):
    ...

app = App(..., ignore_self=False)  # 자기 메시지까지 받으려면
```

---

## 4. 마이그레이션 절차 (권장)

1. **읽기 전용으로 먼저 돌린다** — `say()` 를 `logger.info()` 로 바꿔 이벤트가
   기대대로 들어오는지만 확인합니다.
2. **경고 로그를 0으로 만든다** — `WARNING` 이상을 켜고 Block Kit 손실,
   미지원 기능, 모드 불일치 경고를 모두 없앱니다.
3. **명령 → 이벤트 → 인터랙션 순으로 켠다** — 이 순서가 디버깅하기 가장 쉽습니다.
4. **`import` 를 정식 경로로 되돌린다** — 검증 단계에서 `compat.install` 을 썼다면
   운영 전에 `from mattermost_bolt import App` 으로 바꾸세요.
   스택 트레이스가 훨씬 명확해집니다.

---

## 5. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 메시지에 반응이 없다 | 봇이 채널에 없음 | 채널에 봇 초대 |
| 슬래시 명령이 `... failed.` | 컨테이너→앱 도달 실패 | §2.2 의 3가지 확인 |
| 버튼을 눌러도 무반응 | `socket` 모드 | `mode="http"` + `request_url` |
| 버튼이 아예 안 보인다 | `request_url` 미지정 | `App(request_url=...)` |
| 굵게가 기울임으로 보인다 | `convert_mrkdwn=False` | 기본값(True) 사용 |
| `ValueError: invalid literal for int()` | `ts` 산술 연산 | §2.1 |
| `MattermostApiError: 403` | 봇 권한 부족 | 봇을 팀·채널에 추가, 토큰 확인 |
| WebSocket 이 계속 재연결 | 토큰 무효 | `client.auth_test()` 로 확인 |
