"""질의 임베딩 서비스 — 글 목록을 받아 정규화 벡터를 돌려준다(CLS 풀링, 96 과 같은 방식)."""
import hmac
import os

import torch
import torch.nn.functional as F
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

MODEL = os.environ.get("MODEL", "/models/KURE-v1")
MAXLEN = int(os.environ.get("MAX_LENGTH", "512"))
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL, dtype=torch.float32).cuda().eval()
app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
MAX_TEXTS = int(os.environ.get("MAX_TEXTS", "256"))      # 한 요청의 글 수·글 길이 상한 — 지나친 요청이 GPU 메모리를 다 쓰지 않게
MAX_CHARS = int(os.environ.get("MAX_CHARS", "8000"))


def _check(authorization: str | None, texts: list[str]) -> None:
    """접근 키(API_KEY 를 준 때만)와 요청 크기."""
    if API_KEY and not hmac.compare_digest(authorization or "", f"Bearer {API_KEY}"):
        raise HTTPException(401, "접근 키가 맞지 않습니다")
    if len(texts) > MAX_TEXTS or any(len(t) > MAX_CHARS for t in texts):
        raise HTTPException(413, "요청이 너무 큽니다")


class Req(BaseModel):
    texts: list[str]


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "dim": int(model.config.hidden_size)}


@app.post("/embed")
def embed(req: Req, authorization: str | None = Header(None)):
    _check(authorization, req.texts)
    if not req.texts:
        return {"vectors": [], "model": MODEL}
    with torch.no_grad():
        b = tok(req.texts, padding=True, truncation=True, max_length=MAXLEN,
                return_tensors="pt").to("cuda")
        v = F.normalize(model(**b).last_hidden_state[:, 0], dim=-1)
    return {"vectors": v.float().cpu().tolist(), "model": MODEL}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8014")), log_level="warning")
