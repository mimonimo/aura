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
# 처리 실패 문서 정리(199) — 지금 읽기 도구로 한 번 더 읽어 보고, 그래도 못 읽으면 문서함에서 뺀다(원본은 원본 보관소에 남음)
scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/199_settle_failed.py >> /tmp/zz_sync.log 2>&1
# 규정 검색 품질 — 규정 조각 수·임베딩이 마지막 측정과 다르면 다시 잰다(215, 화면의 재측정 버튼 대신, 약 1시간)
scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/215_eval_if_stale.py >> /tmp/zz_sync.log 2>&1
# 개인정보 점검 — 마스커 자가 점검은 매일, 저장 본문 잔여 검사는 일요일(216, 화면의 실행 단추 대신)
scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/216_pii_checks.py >> /tmp/zz_sync.log 2>&1
