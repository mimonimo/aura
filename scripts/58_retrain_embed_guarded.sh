#!/bin/bash
# Embed v0 재학습 가드 실행 (Spark).
#
# 2026-09-02 새벽 1차 시도가 137/184 스텝에서 로그도 없이 죽었다.
# 당시 가용 메모리 ~4GB (vLLM이 통합메모리 ~100GB 점유) — OOM 킬 정황.
# 대책: vLLM 컨테이너를 잠시 내려 메모리를 확보하고 학습한 뒤 되살린다.
#
# 실행: nohup bash scripts/58_retrain_embed_guarded.sh > /tmp/retrain-guard.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

echo "[$(date +%T)] vLLM 정지 (메모리 확보)"
docker stop vllm-smoke
sync
echo 3 | sudo -n tee /proc/sys/vm/drop_caches > /dev/null 2>&1 || echo "drop_caches 생략(sudo 불가)"
free -g | sed -n 2p

echo "[$(date +%T)] 학습 시작"
ZZAIMY_EMBED_DEVICE=cpu .venv-train/bin/python scripts/54_train_embed.py \
  > /tmp/train-embed2.log 2>&1
rc=$?
echo "[$(date +%T)] 학습 종료 rc=$rc"

echo "[$(date +%T)] vLLM 재기동"
docker start vllm-smoke
for i in $(seq 1 60); do
  curl -s -o /dev/null --max-time 3 http://127.0.0.1:8000/v1/models && break
  sleep 10
done
curl -s -o /dev/null -w "vLLM: %{http_code}\n" --max-time 5 http://127.0.0.1:8000/v1/models
[ -f docs/embed-v0-report.md ] && echo "리포트 생성됨" || echo "리포트 없음(실패)"
echo "[$(date +%T)] 가드 체인 완료 rc=$rc"
