#!/bin/bash
# 문서 판독(OCR) 도전자 PaddleOCR-VL(0.9B) 을 토르 GPU 에 올린다.
#
# 왜: 검색·리랭킹은 GPU 로 옮겼는데 문서 판독은 아직 VM CPU 라 스캔 PDF 한 건에 수 분이 걸린다
# (재반입에서 큰 문서가 20분 제한에도 걸렸다). 0.9B 라 토르에 가볍게 올라간다.
#
# 채택 판단은 기존 대결 스크립트로 한다(새로 만들지 않는다):
#   기준선  scripts/68_ocr_cer_bench.py  (현행 경로, 어절 F1)
#   도전자  scripts/69_ocr_duel.py <문서id> <쪽수> http://211.170.162.121:8002/v1
#
# 사용 (맥에서):  bash scripts/121_serve_ocr_on_thor.sh [포트]
set -euo pipefail
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
PORT="${1:-8002}"
NAME="zzaimy-ocr-$PORT"
MODEL=/models/PaddleOCR-VL

ssh -p $TP "$THOR" "test -d \$HOME/zzaimy/models/PaddleOCR-VL" \
  || { echo "가중치가 없습니다 — 먼저 내려받으십시오(~/zzaimy/models/PaddleOCR-VL)" >&2; exit 2; }

echo "[$(date +%T)] 판독 서비스 올리기 — 포트 $PORT"
ssh -p $TP "$THOR" "docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --restart unless-stopped --runtime nvidia --ipc host \
  -p $PORT:8000 -e HF_HOME=/root/.cache/huggingface \
  -v \$HOME/zzaimy/hf:/root/.cache/huggingface -v \$HOME/zzaimy/models:/models \
  $IMAGE vllm serve $MODEL --host 0.0.0.0 --port 8000 --served-model-name zzaimy-ocr \
  --max-model-len 8192 --gpu-memory-utilization 0.12 --trust-remote-code >/dev/null"

for i in $(seq 1 40); do
  if ssh -p $TP "$THOR" "curl -s -m 3 http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q zzaimy-ocr; then
    echo "준비됨"; break
  fi
  sleep 15
done
ssh -o BatchMode=yes aura@192.168.16.226 "curl -s -m 5 http://211.170.162.121:$PORT/v1/models | head -c 140 || echo '닿지 않음'"
echo
echo "대결: VM 에서 env PYTHONPATH=src .venv/bin/python scripts/69_ocr_duel.py <문서id> 3 http://211.170.162.121:$PORT/v1"
