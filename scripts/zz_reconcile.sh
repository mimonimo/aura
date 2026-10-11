#!/bin/bash
# 하루 한 번 맞추기 — 연동 점검(175 --find-reimport)이 「DGX 에서 정상 처리됐는데 문서함에 없는 원본」을 찾아 168 --rels 로 다시 들인다.
# 동시 반입 같은 사고로 빠진 원본이 생겨도 저절로 메워지게 한다.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
exec 9>/tmp/zz_parsed.lock
flock 9
scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/175_pipeline_audit.py --find-reimport >> /tmp/zz_sync.log 2>&1
n=$(python3 -c "import json;print(len(json.load(open('data/platform/reimport_rels.json'))))" 2>/dev/null || echo 0)
if [ "$n" -gt 0 ]; then
    echo "맞추기: 다시 들임 $n건" >> /tmp/zz_sync.log
    scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/168_import_parsed.py --rels data/platform/reimport_rels.json data/inbox/parsed/parsed-*.jsonl >> /tmp/zz_sync.log 2>&1
    scripts/zz_run.sh 4G env PYTHONPATH=src .venv/bin/python scripts/175_pipeline_audit.py >> /tmp/zz_sync.log 2>&1
fi
