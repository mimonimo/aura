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
