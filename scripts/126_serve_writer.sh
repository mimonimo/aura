#!/bin/bash
# ③ZZAIMY-Writer(Qwen3.8-27B)를 토르에 올린다 — 어느 토르든, 어느 판(bf16·NVFP4)이든 같은 스크립트로.
#
# 구조(2026-09-21 확정): 토르 02 = 대화·초안(사람이 기다리는 일), 토르 03 = 반입 검토·이미지 판독(배치)
#   + 임베딩·리랭커 서비스. 같은 27B 를 두 대에 각각 올려 배치가 대화를 막지 않게 한다.
#   요청 하나의 속도는 대 수로 늘지 않는다 — 그건 NVFP4(무게 54GB→14GB)로 잡는다.
# 실측(bf16, 토르 02): 동시 3요청에 생성 합계 14 토큰/초, 요청당 ~5 토큰/초.
#
# 사용 (맥에서):
#   HOST=thor-03 MODEL=/models/Qwen3.8-27B-NVFP4 PORT=8001 bash scripts/126_serve_writer.sh
#   HOST=thor-02 MODEL=/models/Qwen3.8-27B       PORT=8001 bash scripts/126_serve_writer.sh   # 지금 판(117 과 같음)
set -euo pipefail
HOST="${HOST:-thor-02}"
case "$HOST" in
  thor-02) THOR=thor-02@211.170.162.120; IP=211.170.162.120 ;;
  thor-03) THOR=thor-03@211.170.162.121; IP=211.170.162.121 ;;
  *) echo "HOST 는 thor-02 또는 thor-03" >&2; exit 2 ;;
esac
TP=8022
IMAGE="${IMAGE:-ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor}"   # Qwen3.8 전용 빌드: qwen3.8-next-jetson-thor-latest
PORT="${PORT:-8001}"
MODEL="${MODEL:-/models/Qwen3.8-27B}"
NAME="zzaimy-writer-$PORT"
SERVED="${SERVED:-zzaimy-writer}"
MAXLEN="${MAXLEN:-16384}"
UTIL="${UTIL:-0.60}"
EXTRA="${EXTRA:-}"                 # 예: --quantization modelopt (NVFP4 판은 설정 파일이 알려 주므로 보통 비워 둔다)

echo "[$(date +%T)] $HOST 서빙 시작 — $MODEL · 문맥 $MAXLEN · 포트 $PORT · 이름 $SERVED"
ssh -p $TP "$THOR" "test -d \$HOME/zzaimy/models/$(basename $MODEL)" \
  || { echo "가중치가 없습니다: $HOST:~/zzaimy/models/$(basename $MODEL)" >&2; exit 2; }
# 이미지마다 진입점이 다르다 — gemma4 빌드는 셸(명령에 vllm 을 써야 함), qwen3.8 빌드는 진입점이 이미 vllm 이다.
# 진입점에 vllm 이 있으면 'serve …' 만 넘긴다(2026-09-21 실측: 'vllm vllm serve' 로 13번 재시작).
EP=$(ssh -p $TP "$THOR" "docker image inspect -f '{{join .Config.Entrypoint \" \"}}' $IMAGE 2>/dev/null")
case "$EP" in
  *serve*) CMD="" ;;            # 진입점이 'vllm serve' — 모델 경로부터 넘긴다
  *vllm*)  CMD="serve" ;;       # 진입점이 'vllm'
  *)       CMD="vllm serve" ;;  # 진입점이 셸
esac
echo "   이미지 진입점: '${EP:-없음}' → 명령 '$CMD'"
ssh -p $TP "$THOR" "docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --restart unless-stopped --runtime nvidia --ipc host \
  -p $PORT:8000 -v \$HOME/zzaimy/models:/models \
  -e HF_HOME=/root/.cache/huggingface -v \$HOME/zzaimy/hf:/root/.cache/huggingface \
  $IMAGE $CMD $MODEL --host 0.0.0.0 --port 8000 --served-model-name $SERVED \
  --max-model-len $MAXLEN --gpu-memory-utilization $UTIL $EXTRA >/dev/null"
echo "[$(date +%T)] 준비 기다리는 중 (27B 적재는 몇 분 걸린다)"
for i in $(seq 1 80); do
  if ssh -p $TP "$THOR" "curl -s -m 3 http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q "$SERVED"; then
    echo "준비됨: $(ssh -p $TP "$THOR" "curl -s http://127.0.0.1:$PORT/v1/models" | head -c 160)"
    break
  fi
  if [ "$i" = 80 ]; then
    echo "준비되지 않음 — 기록:"; ssh -p $TP "$THOR" "docker logs --tail 15 $NAME 2>&1"; exit 1
  fi
  sleep 15
done
echo "[$(date +%T)] 속도 실측 (요청 1개)"
ssh -o BatchMode=yes aura@192.168.16.226 "curl -s -m 600 http://$IP:$PORT/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{\"model\":\"$SERVED\",\"max_tokens\":300,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false},\"messages\":[{\"role\":\"user\",\"content\":\"대학 국고사업 계획서의 추진 체계 절을 300자 안팎으로 써라.\"}]}' \
  -w '\n%{time_total}' | python3 -c \"
import sys, json
raw = sys.stdin.read().rsplit(chr(10), 1); d = json.loads(raw[0]); t = float(raw[1])
n = d['usage']['completion_tokens']; print(f'생성 {n}토큰 / {t:.1f}초 = {n/t:.1f} 토큰/초 (요청 1개)')
print(d['choices'][0]['message']['content'][:100].replace(chr(10), ' '))\""
echo "다음: 화면 '단계별 모델' 에서 용도를 지정하고, scripts/110 으로 점검."
