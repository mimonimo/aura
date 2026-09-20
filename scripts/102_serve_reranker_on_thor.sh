#!/bin/bash
# ②ZZAIMY-Rerank 서빙 — 리랭커를 토르 GPU 에 올린다. VM 은 점수만 물어 온다.
#
# 왜: 운영 VM(CPU)에서 리랭킹은 질의당 3.75초이고, 쌍 길이를 256 토큰으로 줄여 놨는데
# 그 설정에서는 정확한 질문의 순위가 오히려 나빠진다(2026-09-20 짝지은 실측:
# 하이브리드 R@1 0.647 → 리랭커 256 0.640, 512 0.667). GPU 에서는 512 로도 질의당 0.07초다.
# 파인튜닝한 리랭커도 같은 자리에 올려 같은 방식으로 쓴다.
#
# 규약: POST /score {"query": "...", "texts": ["...", ...], "max_length": 512}
#       → {"scores": [float, ...], "model": "..."}  (OpenAI 규격이 아니므로 /v1 아래 두지 않는다)
#
# 사용 (맥에서):  bash scripts/102_serve_reranker_on_thor.sh [포트]
set -euo pipefail
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
PORT="${1:-8013}"
NAME=zzaimy-reranker
MODEL="${MODEL:-/models/bge-reranker-v2-m3}"

ssh -p $TP "$THOR" "mkdir -p ~/zzaimy/serve && cat > ~/zzaimy/serve/reranker.py" <<'PY'
"""리랭커 점수 서비스 — 질의 하나와 후보 글 여러 개를 받아 점수를 돌려준다."""
import os

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = os.environ.get("MODEL", "/models/bge-reranker-v2-m3")
MAXLEN = int(os.environ.get("MAX_LENGTH", "512"))
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL, dtype=torch.float16).cuda().eval()
app = FastAPI()


class Req(BaseModel):
    query: str
    texts: list[str]
    max_length: int | None = None


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "max_length": MAXLEN}


@app.post("/score")
def score(req: Req):
    if not req.texts:
        return {"scores": [], "model": MODEL}
    with torch.no_grad():
        b = tok([req.query] * len(req.texts), req.texts, padding=True, truncation=True,
                max_length=int(req.max_length or MAXLEN), return_tensors="pt").to("cuda")
        s = model(**b).logits.view(-1).float().cpu().tolist()
    return {"scores": s, "model": MODEL, "max_length": int(req.max_length or MAXLEN)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8013")), log_level="warning")
PY

echo "[$(date +%T)] 리랭커 서비스 올리기 — 포트 $PORT"
ssh -p $TP "$THOR" "docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --restart unless-stopped --runtime nvidia --ipc host \
  -e MODEL=$MODEL -e PORT=$PORT -p $PORT:$PORT \
  -v \$HOME/zzaimy/models:/models -v \$HOME/zzaimy/serve:/serve \
  --entrypoint python3 $IMAGE /serve/reranker.py >/dev/null"

echo "[$(date +%T)] 준비 기다리는 중"
for i in $(seq 1 40); do
  if ssh -p $TP "$THOR" "curl -s -m 3 http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok":true'; then
    ssh -p $TP "$THOR" "curl -s http://127.0.0.1:$PORT/health"; echo; break
  fi
  sleep 5
done
echo "[$(date +%T)] VM 에서 닿는지 확인"
ssh -o BatchMode=yes aura@192.168.16.226 "curl -s -m 5 http://211.170.162.121:$PORT/health || echo '닿지 않음 — 방화벽 확인'"
echo
echo "VM 적용: .env.local 에 ZZAIMY_RERANK_URL=http://211.170.162.121:$PORT/score 를 넣고 서비스를 재시작하십시오."
