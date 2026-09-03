#!/bin/bash
# 재색인 체인 — 규정 조각 변경 후 질의 세트·임베딩·앱을 순서대로 갱신한다.
# (조각 재구성 시 파생물 무효화 사고 방지 — 실측 사고 2회의 재발 방지 장치)
set -uo pipefail
cd "$(dirname "$0")/.."
echo "[$(date +%T)] 1/3 합성 질의 재생성"
env PYTHONPATH=src .venv/bin/python scripts/51_synth_queries.py
echo "[$(date +%T)] 2/3 임베딩 재계산"
env PYTHONPATH=src OMP_NUM_THREADS=10 ZZAIMY_EMBED_DEVICE=cpu \
  .venv-train/bin/python scripts/52_embed_chunks.py
rm -f data/platform/.reindex-needed
echo "[$(date +%T)] 3/3 앱 재시작"
pkill -f "[z]zaimy.app.main"; sleep 1
(nohup ~/start-platform.sh > /tmp/platform.log 2>&1 &)
sleep 8
curl -sk -o /dev/null -w "앱: %{http_code}\n" -u zzaimy:password https://localhost:8800/ || true
echo REINDEX_DONE
