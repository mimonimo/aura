#!/bin/bash
# 문서함이 바뀌었으면(.kg-dirty) 1분 안에 원본 보관·그래프·색인을 맞춘다(170 --quick). 반입 잠금(zz_sync)과 따로 —
# 몇 시간 걸리는 반입 중에도 돈다. 후속 처리 자체는 170 안에서 /tmp/zz_post.lock 으로 한 번에 하나.
cd ~/zzaimy-capstone || exit 1
[ -f data/platform/.kg-dirty ] || exit 0
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_quick.lock scripts/zz_run.sh 24G env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --quick >> /tmp/zz_sync.log 2>&1
