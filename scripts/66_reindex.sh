#!/bin/bash
# 재색인 체인 — 규정 조각 변경 후 질의 세트·임베딩·앱을 순서대로 갱신한다.
# (조각 재구성 시 파생물 무효화 사고 방지 — 실측 사고 2회의 재발 방지 장치)
#
# 운영 VM(GPU·LLM 없음) 기준. 합성 질의(51)는 LLM이 필요해 VLLM_BASE_URL이
# 설정된 경우에만 돌린다 — 생략되면 53 평가는 낡은 질의 세트라 무효이며,
# LLM 연결 후 이 체인을 다시 돌려야 한다.
set -uo pipefail
cd "$(dirname "$0")/.."
# 오프라인 자립 — 모델은 로컬 캐시에서만. 미설정 시 허브 접속 재시도로
# 수십 분을 허비한다 (VM은 외부 인터넷 차단, 2026-09-08 실측)
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# 무거운 작업 동시 1개 강제 — 겹치면 즉시 종료 (메모리 포화 사고 방지)
exec 9>/tmp/zzaimy-heavy.lock
flock -n 9 || { echo "다른 무거운 작업이 실행 중 — 중단"; exit 1; }

# 코어 절반만 사용 (전체 할당으로 서버 6시간 먹통 실사고 — HANDOFF 규칙)
NCPU=$(nproc 2>/dev/null || echo 8)
OMP=$(( NCPU / 2 )); [ "$OMP" -lt 1 ] && OMP=1
PY=.venv/bin/python
[ -x .venv-train/bin/python ] && PY=.venv-train/bin/python

if grep -qE '^\s*VLLM_BASE_URL=' .env.local 2>/dev/null; then
  echo "[$(date +%T)] 1/3 합성 질의 재생성"
  env PYTHONPATH=src nice -n 10 .venv/bin/python scripts/51_synth_queries.py
else
  echo "[$(date +%T)] 1/3 합성 질의 생략 (LLM 미연결) — 53 평가는 LLM 연결 후 재생성 필요"
fi

echo "[$(date +%T)] 2/3 임베딩 재계산 (코어 $OMP)"
env PYTHONPATH=src OMP_NUM_THREADS=$OMP MKL_NUM_THREADS=$OMP ZZAIMY_EMBED_DEVICE=cpu \
  nice -n 10 "$PY" scripts/52_embed_chunks.py
rm -f data/platform/.reindex-needed

echo "[$(date +%T)] 3/3 앱 재시작"
if systemctl --user is-enabled zzaimy.service >/dev/null 2>&1; then
  systemctl --user restart zzaimy.service
  sleep 8
  curl -sk -o /dev/null -w "앱: %{http_code}\n" https://localhost/login || true
else
  pkill -f "[z]zaimy.app.main"; sleep 1
  (nohup ~/start-platform.sh > /tmp/platform.log 2>&1 &)
  sleep 8
  curl -sk -o /dev/null -w "앱: %{http_code}\n" https://localhost:8800/login || true
fi
echo REINDEX_DONE
