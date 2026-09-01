"""규정 조각 임베딩 사전 계산 (야간 자동화 / 임베딩 RAG 전환 재료).

KURE-v1(리서치 1안 베이스)로 규정 저장소 전체 조각을 임베딩해 저장한다.
끝나면 프로브 질의 몇 개로 최근접 조각을 로그에 남겨 품질을 눈으로 확인할
수 있게 한다.

실행(Spark): .venv-train/bin/python scripts/52_embed_chunks.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MODEL = "nlpai-lab/KURE-v1"
DB = "data/platform/platform.db"
OUT = Path("data/platform/chunk_embeddings.npz")
META = Path("data/platform/chunk_embeddings.meta.json")

PROBES = [
    "일반휴학 처리 기준",
    "질병 휴학 진단서 제출",
    "채용 응시 제출 서류",
    "평가위원 회피 의무",
    "계약직원 임용 절차",
]


def main() -> None:
    import os

    import numpy as np
    from sentence_transformers import SentenceTransformer

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM regulation_chunks ORDER BY id")]
    print(f"조각 {len(rows)}개 임베딩 시작 ({MODEL})", flush=True)

    # vLLM이 GPU 메모리를 점유 중이라 기본은 CPU (546조각이면 CPU로 충분)
    device = os.environ.get("ZZAIMY_EMBED_DEVICE", "cpu")
    model = SentenceTransformer(MODEL, device=device)
    texts = [f"{r['reg_title']} {r['heading']}\n{r['content'][:1200]}" for r in rows]
    vecs = model.encode(texts, batch_size=16, show_progress_bar=False, normalize_embeddings=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, ids=np.array([r["id"] for r in rows]), vectors=vecs)
    META.write_text(
        json.dumps({"model": MODEL, "n_chunks": len(rows), "dim": int(vecs.shape[1])},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"저장: {OUT} ({vecs.shape})", flush=True)

    for q in PROBES:
        qv = model.encode([q], normalize_embeddings=True)[0]
        sims = vecs @ qv
        top = sims.argsort()[-3:][::-1]
        print(f"\n[프로브] {q}", flush=True)
        for idx in top:
            r = rows[int(idx)]
            print(f"  {sims[idx]:.3f} {r['reg_title'][:20]} | {r['heading'][:40]}", flush=True)


if __name__ == "__main__":
    main()
