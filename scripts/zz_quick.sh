#!/bin/bash
# 1분마다 그래프 입력 지문(문서·조각·갈래·사업 분류·장부·검토 판정)을 지난번과 견주어, 바뀌었으면 그래프·양식·색인을 다시 짓는다
# (170 --quick). 무엇이 어느 길로 바뀌었든 바꾼 쪽이 따로 알리지 않아도 된다. .kg-dirty 는 업로드 원본·밀린 색인처럼 남은 일 표시.
# 반입 잠금(zz_sync)과 따로 — 몇 시간 걸리는 반입 중에도 돈다. 후속 처리 자체는 170 안에서 /tmp/zz_post.lock 으로 한 번에 하나.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_quick.lock scripts/zz_run.sh 24G env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --quick >> /tmp/zz_sync.log 2>&1
