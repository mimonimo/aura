#!/bin/bash
# ③ZZAIMY-Writer 베이스(Qwen3.8-27B)를 토르 02 에 올린다 — 문서 작업(채팅·검토·초안)용.
#
# 구조: DGX = 학습, 토르 = 서빙. 토르 02 가 문서 작업, 토르 03 이 이미지 판독·검색 서빙을 맡는다.
# Ollama 로는 받을 수 없어(0.32.6 이 이 모델 매니페스트를 거절) 가중치를 직접 받아 vLLM 으로 연다.
#
# 사용 (맥에서):  bash scripts/117_serve_writer_on_thor02.sh [포트]
set -euo pipefail
THOR=thor-02@211.170.162.120
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
PORT="${1:-8001}"
NAME="zzaimy-writer-$PORT"
MODEL=/models/Qwen3.8-27B
MAXLEN="${MAXLEN:-16384}"       # 문맥은 필요한 만큼만 — 크게 잡으면 메모리를 먹고 느려진다
UTIL="${UTIL:-0.60}"

echo "[$(date +%T)] 서빙 시작 — $MODEL · 문맥 $MAXLEN · 포트 $PORT"
ssh -p $TP "$THOR" "docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --restart unless-stopped --runtime nvidia --ipc host \
  -p $PORT:8000 -v \$HOME/zzaimy/models:/models \
  $IMAGE --model $MODEL --served-model-name zzaimy-writer \
  --max-model-len $MAXLEN --gpu-memory-utilization $UTIL --dtype bfloat16 >/dev/null"

echo "[$(date +%T)] 준비 기다리는 중 (27B 적재는 몇 분 걸린다)"
for i in $(seq 1 60); do
  if ssh -p $TP "$THOR" "curl -s -m 3 http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q zzaimy-writer; then
    ssh -p $TP "$THOR" "curl -s http://127.0.0.1:$PORT/v1/models" | head -c 200; echo
    break
  fi
  sleep 15
done
echo "[$(date +%T)] VM 에서 닿는지 확인"
ssh -o BatchMode=yes aura@192.168.16.226 "curl -s -m 5 http://211.170.162.120:$PORT/v1/models | head -c 160 || echo '닿지 않음'"
echo
echo "다음: 화면의 '단계별 모델' 에서 문서 작업을 토르 02 · zzaimy-writer 로 지정하십시오."
