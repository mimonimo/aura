#!/bin/bash
# 문서함이 바뀌었으면(.kg-dirty) 1분 안에 원본 보관·그래프·색인을 맞춘다(170 --quick). 10분 주기 전체 동기화와 같은 잠금을 쓴다.
cd ~/zzaimy-capstone || exit 1
[ -f "$(dirname "${ZZAIMY_PLATFORM_SQLITE_PATH:-data/platform/platform.db}")/.kg-dirty" ] || [ -f data/platform/.kg-dirty ] || exit 0
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_sync.lock env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --quick >> /tmp/zz_sync.log 2>&1
