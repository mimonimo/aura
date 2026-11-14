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
