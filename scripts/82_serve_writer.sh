#!/bin/bash
# ZZAIMY-Writer 서빙 — 젯슨 토르에서 vLLM 컨테이너로 OpenAI 호환 규격을 띄운다.
#
# 학습 → 서빙 흐름
#   DGX   scripts/83_sft_writer_qlora.py  → LoRA 어댑터(수백 MB)
#   전달  scripts/94_ship_adapter.sh       → 토르 ~/zzaimy/adapters/<이름>
#   토르  이 스크립트                        → 베이스 모델 + 어댑터를 한 서버에서
# 병합본(27B ≈ 55GB)을 옮기지 않고 어댑터만 옮긴다. 베이스와 학습본이 같은 서버에
# 나란히 올라가므로(모델 이름 zzaimy-base / zzaimy-writer) 베이스라인 대비 개선폭을
# 같은 조건에서 잰다(절대규칙 2).
#
# 확인된 사실 (2026-09-20 토르 03 실측)
#   - 토르에는 pip vLLM 이 없다. NVIDIA 젯슨용 컨테이너
#     ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor (vLLM 0.19.0, torch 2.10, CUDA 가능)를 쓴다.
#   - 계정이 docker 그룹이라 sudo 없이 띄울 수 있다.
#   - 토르 Ollama(0.32.6)는 qwen3.8 GGUF 를 못 읽는다(412, 새 버전 필요).
#   - 컨테이너 기본 HF_HOME 은 /data/models/huggingface 다. 지정하지 않으면 받은 가중치가
#     컨테이너와 함께 사라진다 — HF_HOME 을 고정해 ~/zzaimy/hf 에 남긴다.
#
# 사용 (토르에서):
#   BASE=Qwen/Qwen3-4B bash scripts/82_serve_writer.sh                         # 베이스만
#   BASE=Qwen/Qwen3-4B ADAPTER=writer-sft-01 bash scripts/82_serve_writer.sh    # + 어댑터
# 띄운 뒤 플랫폼에 연결을 등록한다:
#   python scripts/79_register_llm_connection.py --name "토르 서빙" \
#       --base-url http://<토르 주소>:$PORT/v1
set -euo pipefail

BASE="${BASE:-}"                        # 허브 이름 또는 ~/zzaimy/models 아래 경로
ADAPTER="${ADAPTER:-}"                  # ~/zzaimy/adapters 아래 이름
PORT="${PORT:-8000}"
BIND="${BIND:-127.0.0.1}"               # 외부에 열 때만 0.0.0.0 — 기본은 장비 안에서만
MAXLEN="${MAXLEN:-16384}"               # 공고문과 근거를 함께 넣으므로 넉넉히
MEM="${MEM:-0.5}"                       # 통합 메모리 비율 — Ollama 와 나눠 쓴다
QUANT="${QUANT:-}"                      # 예: fp8. 비우면 원본 정밀도
IMAGE="${IMAGE:-ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor}"
NAME="${NAME:-zzaimy-writer}"
ROOTDIR="$HOME/zzaimy"

if [ -z "$BASE" ]; then
  echo "BASE 를 지정하십시오. 예: BASE=Qwen/Qwen3-4B bash scripts/82_serve_writer.sh" >&2
  exit 2
fi
mkdir -p "$ROOTDIR/hf" "$ROOTDIR/models" "$ROOTDIR/adapters"

ARGS=(vllm serve "$BASE" --port 8000 --host 0.0.0.0
      --max-model-len "$MAXLEN" --gpu-memory-utilization "$MEM"
      --served-model-name zzaimy-base)
[ -n "$QUANT" ] && ARGS+=(--quantization "$QUANT")
if [ -n "$ADAPTER" ]; then
  if [ ! -f "$ROOTDIR/adapters/$ADAPTER/adapter_config.json" ]; then
    echo "어댑터가 없습니다 — $ROOTDIR/adapters/$ADAPTER (94_ship_adapter.sh 로 먼저 옮기십시오)" >&2
    exit 2
  fi
  RANK=$(python3 -c "import json;print(json.load(open('$ROOTDIR/adapters/$ADAPTER/adapter_config.json'))['r'])")
  ARGS+=(--enable-lora --max-lora-rank "$RANK"
         --lora-modules "zzaimy-writer=/adapters/$ADAPTER")
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
echo "서빙을 시작합니다 — 베이스 $BASE${ADAPTER:+ · 어댑터 $ADAPTER} · $BIND:$PORT"
docker run -d --name "$NAME" --runtime nvidia --ipc host \
  -p "$BIND:$PORT:8000" \
  -e HF_HOME=/root/.cache/huggingface -v "$ROOTDIR/hf:/root/.cache/huggingface" \
  -v "$ROOTDIR/models:/models:ro" -v "$ROOTDIR/adapters:/adapters:ro" \
  "$IMAGE" "${ARGS[@]}" >/dev/null

# 준비될 때까지 기다렸다가 실제로 모델 목록이 나오는지 확인한다(첫 실행은 가중치 내려받기로 오래 걸린다)
for _ in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    echo "준비되었습니다. 올라온 모델:"
    curl -s "http://127.0.0.1:$PORT/v1/models" | python3 -c \
      'import json,sys; [print("   ", m["id"]) for m in json.load(sys.stdin)["data"]]'
    exit 0
  fi
  docker ps -q -f "name=^$NAME$" | grep -q . || { echo "컨테이너가 멈췄습니다:" >&2; docker logs "$NAME" 2>&1 | tail -20 >&2; exit 1; }
  sleep 10
done
echo "30분 안에 뜨지 않았습니다 — docker logs $NAME 을 확인하십시오." >&2
exit 1
