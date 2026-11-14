#!/usr/bin/env bash
# AURA 모델 서버 띄우기 — GPU 서버에서 직접 실행한다(Docker·NVIDIA 컨테이너 런타임 필요). 앱 서버와 따로 둔다.
#
#   bash deploy/model-server/serve.sh writer --model /srv/models/Qwen3.8-27B-NVFP4 --port 8001
#   bash deploy/model-server/serve.sh embed  --model /srv/models/zzaimy-embed-v2  --port 8016
#   bash deploy/model-server/serve.sh rerank --model /srv/models/zzaimy-rerank-v1 --port 8015
#   bash deploy/model-server/serve.sh status
#
# writer: vLLM OpenAI 호환 서버(문서 작업 에이전트·검토·판독). 앱의 「도구·모델 연결」 → LLM 연결에 http://<GPU 서버>:<포트>/v1 로 잇는다.
# embed·rerank: 질의 임베딩·후보 재정렬 서비스(embedder.py·reranker.py). 앱 「서버 설정」 의 임베딩·리랭커 서버에
#   http://<GPU 서버>:<포트>/embed · /score 로 넣는다. 안 띄우면 앱 서버 CPU 로 대신 돈다(느림).
# 컨테이너는 --restart unless-stopped 라 재부팅 뒤에도 뜬다. 끌 때는 docker stop -t 60 <이름>(강제 종료는 GPU 메모리를 못 돌려받을 수 있다).
# 포트는 기관 내부망에서만 연다 — 인증 없는 끝점을 공인망에 열지 않는다.
set -euo pipefail
KIND="${1:-}"; shift || true
MODEL=""; PORT=""; IMAGE="${IMAGE:-}"; MAXLEN="${MAXLEN:-32768}"; UTIL="${UTIL:-0.85}"; NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2;; --port) PORT="$2"; shift 2;; --image) IMAGE="$2"; shift 2;;
    --max-len) MAXLEN="$2"; shift 2;; --util) UTIL="$2"; shift 2;; --name) NAME="$2"; shift 2;;
    *) echo "모르는 옵션: $1" >&2; exit 2;;
  esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"
# 젯슨(토르)은 NVIDIA 젯슨용 이미지와 --runtime nvidia, 일반 GPU 서버는 공식 이미지와 --gpus all
if [ -f /etc/nv_tegra_release ] || uname -r | grep -qi tegra; then
  GPU=(--runtime nvidia); DEF_VLLM="ghcr.io/nvidia-ai-iot/vllm:qwen3.8-next-jetson-thor-latest"; DEF_PY="$DEF_VLLM"
else
  GPU=(--gpus all); DEF_VLLM="vllm/vllm-openai:latest"; DEF_PY="vllm/vllm-openai:latest"
fi
case "$KIND" in
  status) docker ps --filter name=aura- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'; exit 0;;
  writer|embed|rerank) ;;
  *) sed -n '2,13p' "$0"; exit 2;;
esac
[ -n "$MODEL" ] && [ -d "$MODEL" ] || { echo "--model <가중치 폴더> 가 필요합니다(없음: ${MODEL:-비어 있음})" >&2; exit 2; }
PORT="${PORT:-$( [ "$KIND" = writer ] && echo 8001 || ([ "$KIND" = embed ] && echo 8016 || echo 8015))}"
NAME="${NAME:-aura-$KIND-$PORT}"
MDIR="$(dirname "$MODEL")"; MNAME="$(basename "$MODEL")"
docker stop -t 60 "$NAME" >/dev/null 2>&1 || true; docker rm "$NAME" >/dev/null 2>&1 || true
if [ "$KIND" = writer ]; then
  IMG="${IMAGE:-$DEF_VLLM}"
  EP=$(docker image inspect -f '{{join .Config.Entrypoint " "}}' "$IMG" 2>/dev/null || true)
  case "$EP" in *serve*) CMD=();; *vllm*) CMD=(serve);; *) CMD=(vllm serve);; esac   # 이미지마다 진입점이 다르다
  docker run -d --name "$NAME" --restart unless-stopped "${GPU[@]}" --ipc host -p "$PORT:8000" -v "$MDIR:/models:ro" \
    "$IMG" "${CMD[@]}" "/models/$MNAME" --host 0.0.0.0 --port 8000 --served-model-name zzaimy-writer \
    --max-model-len "$MAXLEN" --gpu-memory-utilization "$UTIL" >/dev/null
  URL="http://127.0.0.1:$PORT/v1/models"; OK='zzaimy-writer'
else
  IMG="${IMAGE:-$DEF_PY}"; APP=$([ "$KIND" = embed ] && echo embedder.py || echo reranker.py)
  docker run -d --name "$NAME" --restart unless-stopped "${GPU[@]}" --ipc host -p "$PORT:$PORT" \
    -e MODEL="/models/$MNAME" -e PORT="$PORT" -v "$MDIR:/models:ro" -v "$HERE:/serve:ro" \
    --entrypoint python3 "$IMG" "/serve/$APP" >/dev/null
  URL="http://127.0.0.1:$PORT/health"; OK='"ok":true'
fi
echo "준비 기다리는 중($NAME — 큰 모델은 몇 분)"
for i in $(seq 1 80); do
  curl -s -m 3 "$URL" 2>/dev/null | grep -q "$OK" && { echo "준비됨: $URL"; break; }
  [ "$i" = 80 ] && { echo "준비되지 않음 — docker logs --tail 30 $NAME" >&2; exit 1; }
  sleep 15
done
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
case "$KIND" in
  writer) echo "앱 「도구·모델 연결」 → LLM 연결 → 서버 추가: http://${IP:-<이 서버>}:$PORT/v1 , 단계별 모델에서 용도 지정";;
  embed)  echo "앱 「서버 설정」 → 임베딩 서버: http://${IP:-<이 서버>}:$PORT/embed  (임베딩 모델을 바꾸면 전체 재색인)";;
  rerank) echo "앱 「서버 설정」 → 리랭커 서버: http://${IP:-<이 서버>}:$PORT/score";;
esac
