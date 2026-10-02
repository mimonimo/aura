#!/bin/bash
# DGX 원본 → VM 갱신(170). cron 과 수동 실행이 겹치지 않게 flock.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
export ZZAIMY_ROLE_CONN="review=92a94f3f,vision=92a94f3f"
exec flock -n /tmp/zz_sync.lock env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --max "${1:-200}" >> /tmp/zz_sync.log 2>&1
