#!/bin/bash
# UI 시각 검수 — 배포 전 필수.
# 로컬로 앱을 띄우고 헤드리스 크롬으로 주요 페이지를 캡처한다.
# 캡처를 눈으로 확인하기 전에는 UI 변경을 배포하지 않는다.
#
# 사용: bash scripts/98_ui_check.sh [출력디렉터리]
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-/tmp/ui-check}"
mkdir -p "$OUT"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pkill -f "zzaimy.app.main" 2>/dev/null || true
nohup .venv/bin/python -m zzaimy.app.main > "$OUT/app.log" 2>&1 &
APP_PID=$!
sleep 4

for spec in "index:/" "chat:/chat" "criteria:/criteria"; do
  name="${spec%%:*}"; path="${spec#*:}"
  "$CHROME" --headless --disable-gpu --window-size=1600,950 \
    --virtual-time-budget=6000 \
    --screenshot="$OUT/ui-$name.png" "http://127.0.0.1:8800$path" 2>/dev/null
  echo "캡처: $OUT/ui-$name.png"
done
# 좁은 창(반응형)도 한 장
"$CHROME" --headless --disable-gpu --window-size=1100,900 \
  --virtual-time-budget=6000 \
  --screenshot="$OUT/ui-index-narrow.png" "http://127.0.0.1:8800/" 2>/dev/null
echo "캡처: $OUT/ui-index-narrow.png"

kill "$APP_PID" 2>/dev/null || true
echo "완료 — 캡처를 확인한 뒤 scripts/99_deploy.sh 로 배포할 것"
