#!/bin/bash
# DGX 원본 → VM 갱신(170). cron 과 수동 실행이 겹치지 않게 flock.
# 매분 불리지만 할 일이 있을 때만 돈다 — DGX 원본 목록 도장(~/parsed/inventory_stamp.json, 170 --parsed 가 1분마다 받음)이 바뀌었거나,
# 앞 회차가 상한(--max)에 걸려 남은 반입이 있을 때(.sync_backlog), 사업 장부·검토 판정이 바뀌었을 때. 그 밖에는 한 시간에 한 번 안전 점검으로만 돈다.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
# 문서 변환 도구가 실행마다 남기는 빈 기록 파일(/tmp/mat-debug-*.log) 정리
find /tmp -maxdepth 1 -name "mat-debug-*.log" -user "$(id -un)" -empty -mmin +60 -delete 2>/dev/null
export ZZAIMY_ROLE_CONN="review=92a94f3f,vision=92a94f3f"
st=data/inbox/parsed/inventory_stamp.json; seen=data/platform/.sync_seen; backlog=data/platform/.sync_backlog
# 사업 장부(kg_external.json)·검토 판정(class_review.jsonl)이 바뀌어도 바로 — 원본의 사업 분류·보관 묶음을 새 장부로 다시 맞춘다
newer=""; for f in "$st" data/platform/kg_external.json data/platform/class_review.jsonl; do [ -f "$f" ] && [ "$f" -nt "$seen" ] && newer=1; done
if [ -f "$seen" ] && [ ! -f "$backlog" ] && [ -z "$newer" ] && [ -z "$(find "$seen" -mmin +59)" ]; then exit 0; fi
flock -n /tmp/zz_sync.lock scripts/zz_run.sh 24G env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py --max "${1:-200}" >> /tmp/zz_sync.log 2>&1 || exit 0
touch "$seen"
# 읽지 못한 원본(지난 점검의 실패 목록)은 정리함 「처리 실패」 갈래로 — 화면에 실패 표를 두지 않는다(10-26). 되돌리기는 217 --restore
scripts/zz_run.sh 2G env PYTHONPATH=src .venv/bin/python scripts/217_clean_failed.py >> /tmp/zz_sync.log 2>&1
# 연동 점검(175) — 원본 → 문서함 → 색인 → 그래프의 빈틈을 주기마다 센다(가벼움, 읽기만)
scripts/zz_run.sh 4G env PYTHONPATH=src .venv/bin/python scripts/175_pipeline_audit.py >> /tmp/zz_sync.log 2>&1
