#!/bin/bash
# 맥 → Spark 코드 배포 (데이터는 절대 건드리지 않는다)
#
# 교훈(2026-09-01): 로컬 테스트로 생긴 빈 platform.db가 rsync로 서버 DB를
# 덮어쓴 사고가 있었다. 서버의 data/는 서버만의 것이다 — 통째로 제외한다.
#
# 사용: bash scripts/99_deploy.sh [--restart]
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=jun@211.170.162.109

rsync -az \
  --exclude '.venv' \
  --exclude '.venv-train' \
  --exclude 'data/' \
  --exclude '.git/' \
  --exclude '__pycache__' \
  ./ "$HOST":~/zzaimy-capstone/

echo "코드 동기화 완료 (data/ 제외)"

if [ "${1:-}" = "--restart" ]; then
  ssh -o BatchMode=yes "$HOST" \
    'pkill -f "[z]zaimy.app.main"; sleep 1; (nohup ~/start-platform.sh > /tmp/platform.log 2>&1 &); sleep 5; PASS=$(cat ~/.zzaimy-pass); curl -sk -u "zzaimy:$PASS" -o /dev/null -w "대시보드: %{http_code}\n" https://localhost:8800/'
fi
