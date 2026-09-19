#!/bin/bash
# ZZAIMY-Writer(27B) 서빙 — OpenAI 호환 규격으로 띄운다.
#
# 쓰는 곳: 서빙 담당 장비(젯슨 토르 211.170.162.120 · .121, 또는 DGX).
# 띄운 뒤 플랫폼에 연결을 등록한다:
#   python scripts/79_register_llm_connection.py --name "토르 서빙" \
#       --base-url http://211.170.162.121:8000/v1
#
# 확인된 사실 (2026-09-19 플랫폼 VM 에서 측정)
#   - DGX 211.170.162.110 은 Ollama(0.33.2) 로 11434 포트에서 서비스 중이다.
#     올라온 모델은 gpt-oss:120b 와 qwen3.6:35b 두 개다.
#   - 토르 .120 은 어느 포트로도 응답하지 않는다. .121 은 8022 만 열려 있다.
#   - 두 토르 모두 추론 포트가 열려 있지 않다. 즉 아직 서빙 중이 아니다.
#
# 미확인
#   - 젯슨 토르(ARM64·Blackwell)에서 vLLM 이 어느 빌드로 도는지 확인하지 못했다.
#     ARM64 용 휠이 없으면 NVIDIA 가 배포하는 젯슨용 컨테이너를 써야 한다.
#   - 27B 를 어느 양자화로 올려야 128GB 안에서 여유가 남는지 측정하지 않았다.
set -euo pipefail

MODEL="${MODEL:-}"                     # 로컬 가중치 경로 또는 허브 이름
PORT="${PORT:-8000}"
MAXLEN="${MAXLEN:-16384}"              # 공고문과 근거를 함께 넣으므로 넉넉히
GPUS="${GPUS:-1}"
QUANT="${QUANT:-}"                     # 예: fp8 · awq. 비우면 원본 정밀도

if [ -z "$MODEL" ]; then
  echo "MODEL 을 지정하십시오. 예:" >&2
  echo "  MODEL=/opt/models/zzaimy-writer-27b bash scripts/82_serve_writer.sh" >&2
  exit 2
fi

ARGS=(--model "$MODEL" --port "$PORT" --host 0.0.0.0
      --max-model-len "$MAXLEN" --tensor-parallel-size "$GPUS"
      --served-model-name zzaimy-writer)
[ -n "$QUANT" ] && ARGS+=(--quantization "$QUANT")

echo "서빙을 시작합니다 — 모델 $MODEL · 포트 $PORT · 길이 $MAXLEN"
python -m vllm.entrypoints.openai.api_server "${ARGS[@]}" &
SERVER_PID=$!

# 준비될 때까지 기다렸다가 실제로 모델 목록이 나오는지 확인한다
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    echo "준비되었습니다. 올라온 모델:"
    curl -s "http://127.0.0.1:$PORT/v1/models" | python3 -c \
      'import json,sys; [print("   ", m["id"]) for m in json.load(sys.stdin)["data"]]'
    wait "$SERVER_PID"
    exit 0
  fi
  sleep 5
done

echo "5분 안에 뜨지 않았습니다. 서버 기록을 확인하십시오." >&2
kill "$SERVER_PID" 2>/dev/null || true
exit 1
