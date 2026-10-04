#!/bin/bash
# 사업 문서 색인(grant_embeddings.npz)만 따로 따라잡는다(170 --index). 그래프 재구축(1분 주기 안)이 길어도 색인은 는다.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_index_run.lock scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --index >> /tmp/zz_sync.log 2>&1
