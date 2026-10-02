#!/bin/bash
# DGX 가 가볍게 처리한 결과(~/parsed)를 5분마다 받아 문서함에 들인다(170 --parsed). 긴 반입(zz_sync)과 잠금이 따로다.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_parsed.lock env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --parsed >> /tmp/zz_sync.log 2>&1
