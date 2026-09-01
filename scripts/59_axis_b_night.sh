#!/bin/bash
# 축 B 리허설 야간 체인 (Spark) — 코퍼스 재구축(2026-09-02) 이후 전체 재실행.
#
# 조각 id가 전부 바뀌어 기존 합성 질의가 무효(54가 학습 쌍 0건으로 실패).
# 순서 규칙(브리프 절대 규칙 2)대로 다시 돈다:
#   1) 51 합성 질의 생성 — vLLM 필요 (기존 파일은 보관본으로 이동)
#   2) 53 검색 베이스라인 측정 — 기준선 먼저
#   3) 58 가드 학습 — vLLM 정지로 메모리 확보 후 학습, 끝나면 복구
#
# 실행: nohup bash scripts/59_axis_b_night.sh > /tmp/axis-b-night.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -f data/interim/synth_queries.jsonl ]; then
  mv data/interim/synth_queries.jsonl \
     "data/interim/synth_queries.$(date +%m%d-%H%M).bak"
  echo "기존 합성 질의를 보관본으로 이동"
fi

echo "[$(date +%T)] 1/3 합성 질의 생성 (51)"
.venv/bin/python scripts/51_synth_queries.py
n=$(grep -c "" data/interim/synth_queries.jsonl 2>/dev/null || echo 0)
echo "[$(date +%T)] 합성 질의 ${n}건"
if [ "$n" -lt 100 ]; then
  echo "질의가 너무 적다 — 중단"; exit 1
fi

echo "[$(date +%T)] 2/3 검색 베이스라인 (53)"
OMP_NUM_THREADS=10 MKL_NUM_THREADS=10 .venv/bin/python scripts/53_retrieval_eval.py \
  || { echo "베이스라인 실패 — 학습 진행 안 함(순서 규칙)"; exit 1; }

echo "[$(date +%T)] 3/3 가드 학습 (58)"
bash scripts/58_retrain_embed_guarded.sh

echo "[$(date +%T)] 축 B 야간 체인 완료"
