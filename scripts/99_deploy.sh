#!/bin/bash
# 맥 → 운영 VM 코드 배포 (데이터는 절대 건드리지 않는다)
#
# 운영 서버 = ESXi VM(aura@192.168.16.226). 기존 Spark(211.xx)는 사용 금지
# (2026-09-07 서버 이전, docs/HANDOFF.md). 맥이 학과망 VPN 안에 있어야 한다.
#
# 교훈(2026-09-01): 로컬 테스트로 생긴 빈 platform.db가 rsync로 서버 DB를
# 덮어쓴 사고가 있었다. 서버의 data/는 서버만의 것이다 — 통째로 제외한다.
#
# 사용: bash scripts/99_deploy.sh [--restart]
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=aura@192.168.16.226

rsync -az \
  --exclude '.venv' \
  --exclude '.venv-train' \
  --exclude 'data/' \
  --exclude '.git/' \
  --exclude '__pycache__' \
  --exclude '.env.local' \
  ./ "$HOST":~/zzaimy-capstone/

echo "코드 동기화 완료 (data/·.env.local 제외)"

if [ "${1:-}" = "--restart" ]; then
  ssh -o BatchMode=yes "$HOST" '
    systemctl --user restart zzaimy.service
    for i in 1 2 3 4 5 6 7 8; do
      code=$(curl -sk -o /dev/null -w "%{http_code}" https://localhost/login)
      [ "$code" = 200 ] && break
      sleep 3
    done
    echo "로그인 페이지: $code"
    [ "$code" = 200 ]'
fi
