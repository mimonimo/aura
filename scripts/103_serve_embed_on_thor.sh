#!/bin/bash
# ①ZZAIMY-Embed 질의 임베딩 서빙 — 검색 때마다 도는 임베딩을 토르 GPU 로 옮긴다.
#
# 왜: 조각 벡터는 배치로 미리 만들어 두지만(96), 질의 벡터는 검색마다 새로 만든다.
# 그 계산이 VM CPU 에서 돌고 있었다. 같은 모델·같은 풀링(CLS+정규화)이라 벡터 공간은 같다
# (96 의 대조에서 코사인 0.99999 확인). 파인튜닝한 임베딩으로 갈아탈 자리도 여기다.
#
# 규약: POST /embed {"texts": ["..."], "model": "(선택)"} → {"vectors": [[...]], "model": "..."}
#
# 사용 (맥에서):  bash scripts/103_serve_embed_on_thor.sh [포트]
set -euo pipefail
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
PORT="${1:-8014}"
NAME=zzaimy-embed
MODEL="${MODEL:-/models/KURE-v1}"

ssh -p $TP "$THOR" "mkdir -p ~/zzaimy/serve && cat > ~/zzaimy/serve/embedder.py" <<'PY'
"""질의 임베딩 서비스 — 글 목록을 받아 정규화 벡터를 돌려준다(CLS 풀링, 96 과 같은 방식)."""
import os

import torch
import torch.nn.functional as F
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

MODEL = os.environ.get("MODEL", "/models/KURE-v1")
MAXLEN = int(os.environ.get("MAX_LENGTH", "512"))
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL, dtype=torch.float32).cuda().eval()
app = FastAPI()


class Req(BaseModel):
    texts: list[str]


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "dim": int(model.config.hidden_size)}


@app.post("/embed")
def embed(req: Req):
    if not req.texts:
        return {"vectors": [], "model": MODEL}
    with torch.no_grad():
        b = tok(req.texts, padding=True, truncation=True, max_length=MAXLEN,
                return_tensors="pt").to("cuda")
        v = F.normalize(model(**b).last_hidden_state[:, 0], dim=-1)
    return {"vectors": v.float().cpu().tolist(), "model": MODEL}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8014")), log_level="warning")
PY

echo "[$(date +%T)] 임베딩 서비스 올리기 — 포트 $PORT · 모델 $MODEL"
ssh -p $TP "$THOR" "docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --restart unless-stopped --runtime nvidia --ipc host \
  -e MODEL=$MODEL -e PORT=$PORT -p $PORT:$PORT \
  -v \$HOME/zzaimy/models:/models -v \$HOME/zzaimy/serve:/serve \
  --entrypoint python3 $IMAGE /serve/embedder.py >/dev/null"

for i in $(seq 1 40); do
  if ssh -p $TP "$THOR" "curl -s -m 3 http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok":true'; then
    ssh -p $TP "$THOR" "curl -s http://127.0.0.1:$PORT/health"; echo; break
  fi
  sleep 5
done
echo "[$(date +%T)] VM 에서 벡터 공간 대조 — 같은 글을 VM CPU 와 토르에서 임베딩해 코사인을 본다"
ssh -o BatchMode=yes aura@192.168.16.226 "cd ~/zzaimy-capstone && env PYTHONPATH=src PORT=$PORT .venv/bin/python - <<'PY'
import json, os, urllib.request
import numpy as np
from zzaimy.app import embed_search

texts = ['산학협력단 업무분장', '연구비 정산 절차는 어떻게 되나요', '지방대학 특성화 사업 신청 자격']
local = embed_search.embed_texts(texts)
req = urllib.request.Request(f\"http://211.170.162.121:{os.environ['PORT']}/embed\",
                             data=json.dumps({'texts': texts}).encode(),
                             headers={'Content-Type': 'application/json'})
remote = np.array(json.load(urllib.request.urlopen(req, timeout=30))['vectors'])
if local is None:
    print('VM 에 임베딩 모델이 없어 대조 불가')
else:
    cos = [float(np.dot(a, b)) for a, b in zip(local, remote)]
    print('코사인', [round(c, 5) for c in cos], '→ 최소', round(min(cos), 5))
PY"
echo
echo "VM 적용: .env.local 에 ZZAIMY_EMBED_URL=http://211.170.162.121:$PORT/embed 를 넣고 서비스를 재시작하십시오."
