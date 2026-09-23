"""corpus_pilot.db 조각 KURE 임베딩 — /dev/corpus 의미검색용 (CPU).

52_embed_chunks의 corpus 판. platform.db를 안 건드리고 corpus_pilot 전용
npz를 만든다. flock·코어 절반으로 감싸 실행.
"""
from __future__ import annotations
import json, sqlite3, os
from pathlib import Path

MODEL = "nlpai-lab/KURE-v1"
from zzaimy.app import paths as _paths  # noqa: E402

DB = str(_paths.corpus_db())
OUT = _paths.corpus_npz()
META = _paths.corpus_meta()

def main():
    import numpy as np
    from sentence_transformers import SentenceTransformer
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM regulation_chunks ORDER BY id")]
    print("코퍼스 조각 " + str(len(rows)) + "개 임베딩 시작 (CPU)", flush=True)
    model = SentenceTransformer(MODEL, device=os.environ.get("ZZAIMY_EMBED_DEVICE", "cpu"))
    texts = [r["reg_title"] + " " + r["heading"] + "\n" + r["content"][:1200] for r in rows]
    vecs = model.encode(texts, batch_size=16, show_progress_bar=False, normalize_embeddings=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, ids=np.array([r["id"] for r in rows]), vectors=vecs)
    META.write_text(json.dumps({"model": MODEL, "n_chunks": len(rows), "dim": int(vecs.shape[1])}, ensure_ascii=False), encoding="utf-8")
    print("저장: " + str(OUT) + " " + str(vecs.shape), flush=True)

if __name__ == "__main__":
    main()
