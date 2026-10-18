#!/bin/bash
# DGX 가 가볍게 처리한 결과(~/parsed)를 1분마다 받아 문서함에 들이고 새 문서의 서류 갈래를 27B(토르 03, review)로 판정한다(170 --parsed).
# 앞 회차가 돌고 있으면 잠금으로 건너뛴다. 긴 반입(zz_sync)과 잠금이 따로다.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_parsed.lock scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --parsed >> /tmp/zz_sync.log 2>&1
