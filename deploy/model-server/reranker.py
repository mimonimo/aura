"""리랭커 점수 서비스 — 질의 하나와 후보 글 여러 개를 받아 점수를 돌려준다."""
import hmac
import os

import torch
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = os.environ.get("MODEL", "/models/bge-reranker-v2-m3")
MAXLEN = int(os.environ.get("MAX_LENGTH", "512"))
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL, dtype=torch.float16).cuda().eval()
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
    query: str
    texts: list[str]
    max_length: int | None = None


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "max_length": MAXLEN}


@app.post("/score")
def score(req: Req, authorization: str | None = Header(None)):
    _check(authorization, [req.query, *req.texts])
    if not req.texts:
        return {"scores": [], "model": MODEL}
    with torch.no_grad():
        b = tok([req.query] * len(req.texts), req.texts, padding=True, truncation=True,
                max_length=min(int(req.max_length or MAXLEN), MAXLEN), return_tensors="pt").to("cuda")
        s = model(**b).logits.view(-1).float().cpu().tolist()
    return {"scores": s, "model": MODEL, "max_length": min(int(req.max_length or MAXLEN), MAXLEN)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8013")), log_level="warning")
