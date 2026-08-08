"""S1 — WebSocket 이벤트 수신 검증.

봇 토큰으로 Mattermost WebSocket 에 붙어 프레임을 그대로 덤프한다.
정규화 로직과 테스트 픽스처의 근거가 되는 **실측 페이로드**를 남기는 것이 목적이다.

    export MM_SERVER_URL=https://mattermost.example.com
    export MM_BOT_TOKEN=...
    uv run python spikes/s1_websocket.py [지속시간초]

실행 중에 대상 채널에 메시지를 쓰거나 이모지를 달면 프레임이 찍힌다.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Windows 콘솔 기본 인코딩(cp949)에서 한글·이모지 출력이 깨지지 않도록 고정한다.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from mattermost_bolt.adapter.ws_client import MattermostWebSocketClient
from mattermost_bolt.payload.event import normalize_ws_event
from mattermost_bolt.ts import TsCodec

OUT_DIR = Path(__file__).parent / "payloads"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0

# 잡음이 많아 덤프에서 제외하는 이벤트.
NOISY = {"status_change", "typing", "preferences_changed", "channel_viewed"}


def main() -> int:
    server = os.environ.get("MM_SERVER_URL")
    token = os.environ.get("MM_BOT_TOKEN")
    if not (server and token):
        print("MM_SERVER_URL / MM_BOT_TOKEN 환경변수가 필요합니다.", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(exist_ok=True)
    codec = TsCodec()
    seen: dict[str, int] = {}

    def on_event(frame: dict) -> None:
        name = frame.get("event", "?")
        seen[name] = seen.get(name, 0) + 1
        if name in NOISY:
            return
        print(f"\n=== {name} (seq={frame.get('seq')}) ===")
        print(json.dumps(frame, ensure_ascii=False, indent=2)[:1500])
        normalized = normalize_ws_event(frame, codec)
        if normalized:
            print("--- 정규화 결과 ---")
            print(json.dumps(normalized, ensure_ascii=False, indent=2)[:1500])
        # 이벤트 종류별 첫 샘플만 파일로 남긴다.
        target = OUT_DIR / f"ws_{name}.json"
        if not target.exists():
            target.write_text(json.dumps(frame, ensure_ascii=False, indent=2), encoding="utf-8")

    client = MattermostWebSocketClient(token=token, server_url=server, on_event=on_event)
    thread = threading.Thread(target=client.run_forever, daemon=True)
    thread.start()

    if not client.connected.wait(15):
        print("인증 실패 또는 연결 타임아웃", file=sys.stderr)
        client.close()
        return 1

    print(f"연결 성공. {DURATION:.0f}초 동안 이벤트를 수집합니다.")
    print("대상 채널에 메시지를 쓰거나 이모지를 달아보세요.")
    time.sleep(DURATION)
    client.close()

    print("\n=== 수신 요약 ===")
    for name, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {name:28s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
