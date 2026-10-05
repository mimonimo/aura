#!/bin/bash
# DGX 주기 처리(크론, 30분) — 원본 목록 갱신(165) → 새로 생기거나 바뀐 원본만 가볍게 처리(167). 결과(~/parsed)는 VM 이 5분마다 들인다(170 --parsed).
# 10/5: 원본 목록이 10/3 것 그대로라 그 뒤 원본 2만여 건이 처리 대상에 오르지 않았다(VM 원본 장부 199,466 / DGX 목록 175,550).
# 범위 밖 경로(지출·증빙·정산·영수·집행·스캔, 절대 규칙 11)는 처리하지 않는다. 메모리 안전장치(작업자 상한·가용 하한)는 167 이 지킨다.
cd ~/zzaimy-capstone || exit 1
mkdir -p ~/.cache/zzaimy-locks && chmod 700 ~/.cache/zzaimy-locks
exec 9>~/.cache/zzaimy-locks/dgx_cycle.lock
flock -n 9 || exit 0
git pull -q 2>/dev/null
export PYTHONPATH=src ZZAIMY_LIGHT_PROCESS=1 ZZAIMY_NO_VISION=1 ZZAIMY_MINERU_SLOTS=2 ZZAIMY_MIN_FREE_GB=12 ZZAIMY_WORKER_MAX_RSS_GB=5 MINERU_MODEL_SOURCE=huggingface
log=~/dgx_cycle.log
echo "== $(date '+%F %T')" >> "$log"
tmp=~/archive_inventory.jsonl.tmp
if .venv-train/bin/python scripts/165_dgx_inventory.py --root ~/data > "$tmp" 2>>"$log" && [ -s "$tmp" ]; then
    mv "$tmp" ~/archive_inventory.jsonl
    echo "원본 목록 $(wc -l < ~/archive_inventory.jsonl)건" >> "$log"
else
    rm -f "$tmp"; echo "원본 목록 갱신 실패 — 지난 목록으로" >> "$log"
fi
.venv-parse/bin/python scripts/167_dgx_parse.py --inventory ~/archive_inventory.jsonl --skip ~/ingested.txt --out ~/parsed \
    --workers 4 --exclude '지출|증빙|스캔|영수|정산|집행' --doc-timeout 900 2>&1 | grep --line-buffered -E "^대상|PARSE_|상한" >> "$log"
