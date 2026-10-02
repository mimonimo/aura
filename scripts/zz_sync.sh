#!/bin/bash
# DGX 원본 → VM 갱신(170). cron 과 수동 실행이 겹치지 않게 flock.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
# 문서 변환 도구가 실행마다 남기는 빈 기록 파일(/tmp/mat-debug-*.log) 정리
find /tmp -maxdepth 1 -name "mat-debug-*.log" -user "$(id -un)" -empty -mmin +60 -delete 2>/dev/null
export ZZAIMY_ROLE_CONN="review=92a94f3f,vision=92a94f3f"
exec flock -n /tmp/zz_sync.lock env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --max "${1:-200}" >> /tmp/zz_sync.log 2>&1
