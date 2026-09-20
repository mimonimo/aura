#!/bin/bash
# 개발 변경 목록(docs/dev-changelog.md) 생성 — git 커밋 이력에서 만든다.
# /dev "전체 변경 목록"과 주간 보고서(_compose_weekly)의 원자료. 수기로 적던 파일이
# 09-03에서 멈춰 화면·보고서가 낡았던 문제의 재발 방지: 배포(99_deploy.sh) 전에 돌린다.
# 커밋되지 않은 작업은 목록에 없다 — 마지막 줄에 그 사실을 남긴다.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/dev-changelog.md
git log --no-merges --pretty='- %ad %s' --date=format:'%m-%d %H:%M' -n 200 > "$OUT"
n_dirty=$(git status --porcelain | wc -l | tr -d ' ')
if [ "$n_dirty" != "0" ]; then
  echo "- (미커밋 변경 ${n_dirty}개 파일 — 커밋 후 목록에 반영)" >> "$OUT"
fi
echo "dev-changelog.md: $(wc -l < "$OUT") 줄"
