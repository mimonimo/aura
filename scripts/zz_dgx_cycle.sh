#!/bin/bash
# DGX 주기 처리(크론, 1분 — 앞 회차가 돌면 잠금으로 건너뜀) — 원본 목록·파일 사실 장부 갱신(165) → 새로 생기거나 바뀐 원본만 가볍게 처리(167).
# 결과(~/parsed)는 VM 이 1분마다 들이고(170 --parsed) 서류 갈래 27B 판정도 VM 이 토르 03(review)으로 한다 — DGX 는 상주 27B 를 띄우지 않는다.
# 학습(verify_writer_sft_on_dgx.sh)이 같은 잠금을 쥐면 그동안 건너뛴다.
# 원본 목록을 주기마다 새로 만들어야 새로 생긴 원본이 처리 대상에 오른다.
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
# 165 는 파일 사실 장부(~/parsed/archive_facts.jsonl: 내용 지문·실제 형식)를 쌓는다 — VM(170)이 받아 같은 중복 판정을 한다.
# 옛 오피스·한글 이진 판별(olefile)이 있는 처리용 환경(.venv-parse)으로 돈다
mkdir -p ~/parsed
if .venv-parse/bin/python scripts/165_dgx_inventory.py --root ~/data --facts ~/parsed/archive_facts.jsonl > "$tmp" 2>>"$log" && [ -s "$tmp" ]; then
    mv "$tmp" ~/archive_inventory.jsonl
    echo "원본 목록 $(wc -l < ~/archive_inventory.jsonl)건" >> "$log"
else
    rm -f "$tmp"; echo "원본 목록 갱신 실패 — 지난 목록으로" >> "$log"
fi
.venv-parse/bin/python scripts/167_dgx_parse.py --inventory ~/archive_inventory.jsonl --skip ~/ingested.txt --out ~/parsed \
    --workers 4 --exclude '지출|증빙|스캔|영수|정산|집행' --doc-timeout 900 2>&1 | grep --line-buffered -E "^대상|PARSE_|상한" >> "$log"
# 167 이 끝난 뒤 남은 MinerU 판독 서버(부모가 끝나 고아가 된 것)를 내린다 — 다음 판독 때 다시 뜬다. 학습과 메모리를 다투지 않게.
for pid in $(pgrep -u "$USER" -f "mineru.cli.fast_api"); do
    pp=$(ps -o ppid= -p "$pid" | tr -d ' '); pc=$(ps -o comm= -p "$pp" 2>/dev/null)
    case "$pc" in systemd|init) kill "$pid" && echo "MinerU 서버 내림 $pid" >> "$log";; esac
done
# 학습셋 목록(설명 정보만 — 이름·상태·건수·지문·승인 범위) — VM 데이터·학습 화면이 같은 ~/parsed 길로 받아 보인다. 원문은 DGX 밖으로 나가지 않는다
python3 scripts/197_trainset_catalog.py 2>&1 | grep --line-buffered "^TRAINSETS" >> "$log"
