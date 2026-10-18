#!/bin/bash
# DGX 원본 → VM 갱신(170). cron 과 수동 실행이 겹치지 않게 flock.
# 매분 불리지만 DGX 원본 목록 도장(~/parsed/inventory_stamp.json, 170 --parsed 가 1분마다 받음)이 바뀌었을 때 바로 돌고,
# 바뀐 게 없으면 10분에 한 번만(남은 반입 이어 하기·연동 점검) 돈다.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
# 문서 변환 도구가 실행마다 남기는 빈 기록 파일(/tmp/mat-debug-*.log) 정리
find /tmp -maxdepth 1 -name "mat-debug-*.log" -user "$(id -un)" -empty -mmin +60 -delete 2>/dev/null
export ZZAIMY_ROLE_CONN="review=92a94f3f,vision=92a94f3f"
st=data/inbox/parsed/inventory_stamp.json; seen=data/platform/.sync_seen
if [ -f "$seen" ] && { [ ! -f "$st" ] || [ ! "$st" -nt "$seen" ]; } && [ -z "$(find "$seen" -mmin +9)" ]; then exit 0; fi
flock -n /tmp/zz_sync.lock scripts/zz_run.sh 24G env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --max "${1:-200}" >> /tmp/zz_sync.log 2>&1 || exit 0
touch "$seen"
# 연동 점검(175) — 원본 → 문서함 → 색인 → 그래프의 빈틈을 주기마다 센다(가벼움, 읽기만)
scripts/zz_run.sh 4G env PYTHONPATH=src .venv/bin/python scripts/175_pipeline_audit.py >> /tmp/zz_sync.log 2>&1
