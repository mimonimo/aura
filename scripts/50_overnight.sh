#!/bin/bash
# 야간 자동화 (Spark 단독 실행 — 노트북·VPN 연결과 무관)
# 실행: nohup bash scripts/50_overnight.sh > /tmp/overnight.log 2>&1 &
# 중지: touch /tmp/stop-overnight
set -u
cd "$(dirname "$0")/.."
LOG_PREFIX() { date "+%m-%d %H:%M:%S"; }
say() { echo "[$(LOG_PREFIX)] $*"; }

rm -f /tmp/stop-overnight

ensure_vllm() {
  if ! curl -s -o /dev/null --max-time 5 http://localhost:8000/v1/models; then
    say "vLLM 다운 감지 — 재기동"
    docker start vllm-smoke 2>/dev/null || true
    for _ in $(seq 1 40); do
      sleep 15
      curl -s -o /dev/null --max-time 5 http://localhost:8000/v1/models && break
    done
  fi
}

ensure_platform() {
  if ! curl -sk -o /dev/null --max-time 5 https://localhost:8800/; then
    say "대시보드 다운 감지 — 재기동"
    pkill -f "[z]zaimy.app.main" 2>/dev/null
    sleep 1
    (nohup "$HOME/start-platform.sh" > /tmp/platform.log 2>&1 &)
  fi
}

say "=== 야간 작업 시작 ==="
ensure_vllm
ensure_platform

say "[1/4] 합성 질의 생성 (규정 조각 전체)"
.venv/bin/python scripts/51_synth_queries.py || say "합성 질의 생성 실패(계속 진행)"

say "[2/4] KURE-v1 임베딩 사전 계산"
.venv-train/bin/python scripts/52_embed_chunks.py || say "임베딩 계산 실패(계속 진행)"

say "[3/4] 모델 웨이트 선다운로드"
for repo in BAAI/bge-reranker-v2-m3 Qwen/Qwen3.5-35B-A3B Qwen/Qwen3.5-35B-A3B-Instruct; do
  [ -f /tmp/stop-overnight ] && break
  say "다운로드 시도: $repo"
  .venv-train/bin/huggingface-cli download "$repo" --quiet \
    && say "완료: $repo" || say "실패 또는 없음: $repo (건너뜀)"
done

say "[4/4] 서비스 감시 루프 진입 (5분 간격, stop 파일로 종료)"
while [ ! -f /tmp/stop-overnight ]; do
  ensure_vllm
  ensure_platform
  sleep 300
done
say "=== stop 파일 감지 — 야간 작업 종료 ==="
