#!/bin/bash
# 운영 VM 배포 — 정본은 깃허브 하나다. VM 은 원격에서 받아 그 상태 그대로 돌린다.
#
# 왜 바꿨나(2026-09-20): 예전에는 맥 → VM rsync 로 파일만 밀고, 깃 이력은 VM 에만 쌓았다.
# 정본이 두 곳으로 갈라져 "무엇이 원본인지" 매번 확인해야 했고, 실행 중 스크립트를 덮어써
# 사고도 났다. VM 이 외부로 나갈 수 있게 되면서(9/17 개방) 깃 기반으로 통일한다.
#
# 흐름:  맥에서 고친다 → 커밋·푸시 → 이 스크립트가 VM 에서 받아 재시작한다.
# 데이터(`data/`)와 `.env.local` 은 원래 깃에 없다 — VM 것이 그대로 남는다.
#
# 사용:
#   bash scripts/99_deploy.sh              # 받아서 적용만
#   bash scripts/99_deploy.sh --restart    # 적용 후 서비스 재시작·확인
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=aura@192.168.16.226
BRANCH="${BRANCH:-main}"

# 1) 배포는 '푸시한 커밋'만 나간다. 작업 트리에 미커밋 변경이 있어도 그건 나가지 않으므로
#    멈추지 않고 알리기만 한다 — 같은 트리에서 다른 사람(Codex)이 작업 중일 수 있다.
if [ -n "$(git status --porcelain)" ]; then
  echo "알림: 커밋하지 않은 변경이 있습니다(배포에는 포함되지 않습니다):" >&2
  git status --short | head -10 >&2
fi
LOCAL=$(git rev-parse HEAD)
if ! git merge-base --is-ancestor "$LOCAL" "origin/$BRANCH" 2>/dev/null; then
  echo "현재 커밋이 origin/$BRANCH 에 없습니다. 먼저 push 하십시오 (git push origin $BRANCH)." >&2
  exit 2
fi

# 2) VM 에서 받아 그 커밋으로 맞춘다. VM 에서 직접 고친 것이 있으면 멈춘다(덮어쓰지 않는다)
ssh -o BatchMode=yes "$HOST" "cd ~/zzaimy-capstone && \
  if [ -n \"\$(git status --porcelain)\" ]; then \
    echo 'VM 에 커밋하지 않은 변경이 있습니다 — 확인 후 다시 배포하십시오:' >&2; \
    git status --short | head -10 >&2; exit 3; \
  fi; \
  git fetch --prune -q origin && git checkout -q -B $BRANCH $LOCAL && \
  echo \"VM 적용: \$(git log --oneline -1)\""

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
