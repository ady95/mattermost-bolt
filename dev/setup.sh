#!/usr/bin/env bash
# 개발용 Mattermost 리소스를 만든다 (재실행 안전).
#
#   팀 bolt / 채널 bolt-dev / 봇 boltbot + 액세스 토큰
#
# compose 를 `down -v` 로 초기화한 뒤에도 이 스크립트 한 번으로 복구된다.
#
#   MM_SERVER_URL=https://mattermost.example.com \
#   MM_ADMIN_LOGIN=admin@test.local MM_ADMIN_PASSWORD=... \
#   ./dev/setup.sh
#
# 결과 토큰은 dev/.tokens.env (chmod 600, .gitignore) 에 기록된다.

set -euo pipefail

SERVER="${MM_SERVER_URL:?MM_SERVER_URL 이 필요합니다}"
LOGIN="${MM_ADMIN_LOGIN:?MM_ADMIN_LOGIN 이 필요합니다}"
PASSWORD="${MM_ADMIN_PASSWORD:?MM_ADMIN_PASSWORD 가 필요합니다}"

TEAM="${MM_TEAM:-bolt}"
TEAM_DISPLAY="${MM_TEAM_DISPLAY:-Bolt Test}"
CHANNEL="${MM_TEST_CHANNEL:-bolt-dev}"
BOT="${MM_BOT_USERNAME:-boltbot}"

API="$SERVER/api/v4"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKENS_FILE="$HERE/.tokens.env"

need() { command -v "$1" >/dev/null || { echo "$1 이 필요합니다" >&2; exit 1; }; }
need curl
need python3

json() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1',''))" 2>/dev/null || true; }

echo "== 관리자 로그인 =="
ADMIN_TOKEN="$(curl -sS -D - -o /dev/null \
  -H 'Content-Type: application/json' \
  -d "{\"login_id\":\"$LOGIN\",\"password\":\"$PASSWORD\"}" \
  "$API/users/login" | tr -d '\r' | awk '/^[Tt]oken:/{print $2}')"
[ -n "$ADMIN_TOKEN" ] || { echo "로그인 실패" >&2; exit 1; }
AUTH=(-H "Authorization: Bearer $ADMIN_TOKEN")
echo "   OK"

echo "== 팀 $TEAM =="
TEAM_ID="$(curl -sS "${AUTH[@]}" "$API/teams/name/$TEAM" | json id)"
if [ -z "$TEAM_ID" ]; then
  TEAM_ID="$(curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$TEAM\",\"display_name\":\"$TEAM_DISPLAY\",\"type\":\"O\"}" \
    "$API/teams" | json id)"
  echo "   생성됨 $TEAM_ID"
else
  echo "   기존 사용 $TEAM_ID"
fi
[ -n "$TEAM_ID" ] || { echo "팀 생성 실패" >&2; exit 1; }

echo "== 채널 $CHANNEL =="
CHANNEL_ID="$(curl -sS "${AUTH[@]}" "$API/teams/$TEAM_ID/channels/name/$CHANNEL" | json id)"
if [ -z "$CHANNEL_ID" ]; then
  CHANNEL_ID="$(curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "{\"team_id\":\"$TEAM_ID\",\"name\":\"$CHANNEL\",\"display_name\":\"$CHANNEL\",\"type\":\"O\"}" \
    "$API/channels" | json id)"
  echo "   생성됨 $CHANNEL_ID"
else
  echo "   기존 사용 $CHANNEL_ID"
fi

echo "== 봇 $BOT =="
BOT_ID="$(curl -sS "${AUTH[@]}" "$API/users/username/$BOT" | json id)"
if [ -z "$BOT_ID" ]; then
  BOT_ID="$(curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$BOT\",\"display_name\":\"Mattermost Bolt\",\"description\":\"mattermost-bolt dev bot\"}" \
    "$API/bots" | json user_id)"
  echo "   생성됨 $BOT_ID"
else
  echo "   기존 사용 $BOT_ID"
fi
[ -n "$BOT_ID" ] || { echo "봇 생성 실패 (EnableBotAccountCreation 확인)" >&2; exit 1; }

echo "== 팀·채널 배정 =="
curl -sS -o /dev/null "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d "{\"team_id\":\"$TEAM_ID\",\"user_id\":\"$BOT_ID\"}" "$API/teams/$TEAM_ID/members"
curl -sS -o /dev/null "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$BOT_ID\"}" "$API/channels/$CHANNEL_ID/members"
echo "   완료"

echo "== 봇 토큰 =="
BOT_TOKEN="$(curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"description":"mattermost-bolt dev"}' "$API/users/$BOT_ID/tokens" | json token)"
[ -n "$BOT_TOKEN" ] || { echo "토큰 발급 실패 (EnableUserAccessTokens 확인)" >&2; exit 1; }
echo "   발급됨"

umask 077
cat > "$TOKENS_FILE" <<EOF
# ./dev/setup.sh 가 생성했습니다. 커밋하지 마세요.
MM_SERVER_URL=$SERVER
MM_BOT_TOKEN=$BOT_TOKEN
MM_ADMIN_TOKEN=$ADMIN_TOKEN
MM_TEAM=$TEAM
MM_TEST_CHANNEL=$CHANNEL
MM_TEST_CHANNEL_ID=$CHANNEL_ID
MM_BOT_USER_ID=$BOT_ID
EOF
chmod 600 "$TOKENS_FILE"

cat <<EOF

완료. 자격증명은 $TOKENS_FILE (chmod 600) 에 있습니다.

  set -a && . ./dev/.tokens.env && set +a
  python examples/01_hello_message.py

인터랙션(버튼·모달)까지 쓰려면 Mattermost 가 이 호스트에 도달할 수 있어야 합니다.
사설 IP 는 기본 차단이므로 아래를 확인하세요.

  mmctl config set ServiceSettings.AllowedUntrustedInternalConnections "<이 호스트 IP>"
EOF
