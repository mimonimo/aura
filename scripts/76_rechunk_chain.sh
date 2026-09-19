#!/bin/bash
# 조각 재분할 체인(운영 VM): 백업+재분할 → 임베딩 재계산·앱 재시작(66) → 개체 재추출(70).
# 재분할은 수 초, 임베딩은 수 분(코어 절반). 66이 무거운 작업 잠금을 잡는다.
#
# 실행: nohup bash scripts/76_rechunk_chain.sh > /tmp/rechunk.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PY=.venv/bin/python

echo "[$(date +%T)] 1/3 규정 조각 재분할(백업 포함)"
env PYTHONPATH=src "$PY" scripts/75_rechunk_regulations.py || { echo "재분할 실패 — 중단"; exit 1; }

echo "[$(date +%T)] 2/3 재색인 체인(66)"
bash scripts/66_reindex.sh || { echo "재색인 실패"; exit 1; }

echo "[$(date +%T)] 3/3 개체 재추출(70)"
env PYTHONPATH=src "$PY" scripts/70_extract_entities.py || echo "개체 재추출 실패(그래프만 영향)"
echo "[$(date +%T)] RECHUNK_CHAIN_DONE"
