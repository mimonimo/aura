#!/bin/bash
# KURE-v2 다중 벡터 검색 서비스 — 128 이 만든 색인을 GPU 에 올려 놓고 질의마다 MaxSim 으로 순위를 낸다.
# 운영 검색(어휘 + 임베딩 :8016 + 리랭커 :8015)과 나란히 두는 비교용 서비스다. 채택 전에는 운영이 부르지 않는다.
#
# 규약: GET /health · POST /search {"query": "...", "top_k": 20} → {"ids": [...], "scores": [...]}
#       POST /score {"query": "...", "ids": [...]} → {"scores": [...]}   (후보 재정렬 비교용)
# 사용 (맥에서):  bash scripts/129_serve_kure2_on_thor.sh [포트=8017]
set -euo pipefail
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
PORT="${1:-8017}"
NAME="zzaimy-kure2-$PORT"
MODEL="${MODEL:-/models/KURE-v2}"
INDEX=/home/thor-03/zzaimy/index/kure2

ssh -p $TP "$THOR" "test -f $INDEX/tokens.npy" || { echo "색인이 없습니다 — 먼저 scripts/128_kure2_index_on_thor.sh" >&2; exit 2; }
ssh -p $TP "$THOR" "cat > ~/zzaimy/serve/kure2_search.py" <<'PY'
"""KURE-v2 다중 벡터 검색 서비스 — 색인 전체를 GPU 에 두고 질의 토큰 × 조각 토큰 MaxSim 을 한 번에 계산한다."""
import json
import os
import sys
import time

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

sys.path.insert(0, "/serve")
from colbert_encoder import ColbertEncoder, maxsim

MODEL = os.environ.get("MODEL", "/models/KURE-v2")
INDEX = os.environ.get("INDEX", "/index")
meta = json.load(open(f"{INDEX}/meta.json"))
enc = ColbertEncoder(MODEL, doc_len=int(meta.get("doc_len", 1024)))
tokens = torch.from_numpy(np.load(f"{INDEX}/tokens.npy")).cuda()          # [N, L, dim] fp16
lens = torch.from_numpy(np.load(f"{INDEX}/lens.npy")).cuda()
ids = np.load(f"{INDEX}/ids.npy")
pos = {int(c): i for i, c in enumerate(ids)}
keep = torch.arange(tokens.shape[1], device="cuda")[None, :] < lens[:, None]   # [N, L]
app = FastAPI()


class SearchReq(BaseModel):
    query: str
    top_k: int = 20


class ScoreReq(BaseModel):
    query: str
    ids: list[int]


@app.get("/health")
def health():
    return {"ok": True, "model": meta.get("model"), "n_chunks": int(len(ids)), "dim": enc.dim,
            "max_tokens": int(tokens.shape[1]), "index_bytes": int(tokens.numel() * 2)}


@app.post("/search")
def search(req: SearchReq):
    t0 = time.time()
    q = enc.encode_queries([req.query])[0]
    s = maxsim(q, tokens, keep)
    k = max(1, min(req.top_k, len(ids)))
    top = torch.topk(s, k)
    return {"ids": [int(ids[i]) for i in top.indices.tolist()], "scores": [round(float(v), 4) for v in top.values.tolist()],
            "ms": round((time.time() - t0) * 1000, 1)}


@app.post("/score")
def score(req: ScoreReq):
    q = enc.encode_queries([req.query])[0]
    sel = [pos[c] for c in req.ids if c in pos]
    if not sel:
        return {"scores": []}
    idx = torch.tensor(sel, device="cuda")
    s = maxsim(q, tokens[idx], keep[idx])
    out = dict(zip(sel, s.tolist()))
    return {"scores": [round(out.get(pos.get(c, -1), 0.0), 4) for c in req.ids]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8017")), log_level="warning")
PY

echo "[$(date +%T)] KURE-v2 검색 서비스 올리기 — 포트 $PORT"
ssh -p $TP "$THOR" "docker stop -t 20 $NAME >/dev/null 2>&1; docker rm $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --restart unless-stopped --runtime nvidia --ipc host \
  -e MODEL=$MODEL -e PORT=$PORT -e INDEX=/index -p $PORT:$PORT \
  -v \$HOME/zzaimy/models:/models:ro -v \$HOME/zzaimy/serve:/serve:ro -v $INDEX:/index:ro \
  --entrypoint python3 $IMAGE /serve/kure2_search.py >/dev/null"
for i in $(seq 1 60); do
  if ssh -p $TP "$THOR" "curl -s -m 3 http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok":true'; then
    ssh -p $TP "$THOR" "curl -s http://127.0.0.1:$PORT/health; echo; curl -s -m 30 -X POST http://127.0.0.1:$PORT/search -H 'Content-Type: application/json' -d '{\"query\":\"연구비 정산 절차는 어떻게 되나요\",\"top_k\":5}'"; echo
    exit 0
  fi
  sleep 5
done
echo "준비되지 않음 — docker logs $NAME" >&2; ssh -p $TP "$THOR" "docker logs --tail 30 $NAME" >&2; exit 1
