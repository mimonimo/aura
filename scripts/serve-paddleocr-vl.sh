#!/usr/bin/env bash
# PaddleOCR-VL 0.9B 도전자 서빙 — Spark에서 실행.
# dots.ocr 스모크(09-02)에서 검증된 구성 재사용: vllm-smoke와 같은 nightly
# 이미지, 별도 컨테이너, 127.0.0.1 바인딩(공인 IP 노출 금지), gpu-mem 낮게.
# 0.9B라 0.08(≈9.6GiB)이면 충분 — 가용범위를 꽉 채우지 않는다.
#
# 사용: bash scripts/serve-paddleocr-vl.sh        # 기동
#       bash scripts/serve-paddleocr-vl.sh stop   # 종료·정리
set -euo pipefail

NAME=paddleocr-vl
PORT=8002
MODEL_DIR="$HOME/models/PaddleOCR-VL"

if [[ "${1:-}" == "stop" ]]; then
  docker rm -f "$NAME" 2>/dev/null || true
  echo "중지 완료"
  exit 0
fi

# vllm-smoke가 쓰는 검증 이미지(다이제스트 고정)를 그대로 사용
IMAGE=$(docker inspect vllm-smoke --format '{{.Config.Image}}')
echo "이미지: $IMAGE"

if [[ ! -f "$MODEL_DIR/config.json" ]]; then
  echo "모델 다운로드 (~2GB)..."
  ~/zzaimy-capstone/.venv/bin/hf download PaddlePaddle/PaddleOCR-VL \
    --local-dir "$MODEL_DIR"
fi

docker rm -f "$NAME" 2>/dev/null || true
docker run -d --name "$NAME" --gpus all \
  --memory 24g \
  -p 127.0.0.1:$PORT:8000 \
  -v "$MODEL_DIR":/model \
  "$IMAGE" \
  --model /model --served-model-name paddleocr-vl \
  --trust-remote-code \
  --gpu-memory-utilization 0.08 \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0

echo "기동 대기..."
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:$PORT/v1/models >/dev/null 2>&1; then
    echo "READY (${i}0초)"
    exit 0
  fi
  sleep 10
done
echo "TIMEOUT — docker logs $NAME 확인"
exit 1
