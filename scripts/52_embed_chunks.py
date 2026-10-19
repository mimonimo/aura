"""규정 조각 임베딩 갱신 (임베딩 RAG 재료).

질의 임베딩과 같은 모델로 조각 벡터를 만든다. 서빙 장비의 임베딩 서비스(ZZAIMY_EMBED_URL, 운영 학습본)가
있으면 그곳에서 계산하고, 없을 때만 이 장비에서 ZZAIMY_EMBED_MODEL 로 계산한다. 색인과 질의 모델이 다르면
벡터 공간이 어긋나므로, 지금 색인과 다른 모델로 계산하게 되면 전부 다시 만든다.

조각 글이 그대로인 벡터는 다시 계산하지 않는다 — 글의 해시(keys)로 맞춰 재사용하고, 사라진 조각은 뺀다.
규정을 몇 건 등록·해제한 뒤에는 바뀐 조각만 계산하므로 몇 초에서 몇 분이면 끝난다.
끝나면 프로브 질의 몇 개로 최근접 조각을 로그에 남겨 품질을 눈으로 확인할 수 있게 한다.

실행: .venv/bin/python scripts/52_embed_chunks.py [--full]   (--full: 재사용 없이 전부 다시)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from zzaimy.app import paths as _paths
from zzaimy.app.database_backend import connect

DB = "data/platform/platform.db"
OUT = _paths.index_npz()
META = _paths.index_meta()
BATCH = 32

PROBES = [
    "일반휴학 처리 기준",
    "질병 휴학 진단서 제출",
    "채용 응시 제출 서류",
    "평가위원 회피 의무",
    "계약직원 임용 절차",
]


def chunk_text(r: dict) -> str:
    return f"{r['reg_title']} {r['heading']}\n{r['content'][:1200]}"


def text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def served_model() -> str | None:
    """임베딩 서비스가 올린 모델 이름(경로의 끝 이름) — 서비스가 없으면 None."""
    url = os.environ.get("ZZAIMY_EMBED_URL", "").strip()
    if not url:
        return None
    import urllib.request

    try:
        with urllib.request.urlopen(url.rsplit("/", 1)[0] + "/health", timeout=10) as r:
            got = json.loads(r.read().decode("utf-8"))
        return Path(str(got.get("model") or "")).name or None
    except (OSError, ValueError):
        return None


def load_index() -> tuple[dict, dict]:
    """지금 색인 → ({글 해시: 벡터}, 메타). 해시가 없는 옛 색인은 조각 번호로 맞춘다({('id', n): 벡터})."""
    import numpy as np

    if not OUT.exists() or not META.exists():
        return {}, {}
    meta = json.loads(META.read_text(encoding="utf-8"))
    data = np.load(OUT)
    keys = data["keys"] if "keys" in data.files else None
    ids = data["ids"]
    vecs = data["vectors"]
    if keys is not None:
        return {str(k): v for k, v in zip(keys, vecs)}, meta
    return {("id", int(i)): v for i, v in zip(ids, vecs)}, meta


def main() -> None:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="재사용 없이 전부 다시 계산")
    args = ap.parse_args()

    with connect(DB, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, reg_title, heading, content FROM regulation_chunks ORDER BY id")]
    texts = [chunk_text(r) for r in rows]
    keys = [text_key(t) for t in texts]

    remote_model = served_model()
    model = remote_model or Path(os.environ.get("ZZAIMY_EMBED_MODEL", "nlpai-lab/KURE-v1")).name
    old, meta = ({}, {}) if args.full else load_index()
    if meta and meta.get("model") != model:
        print(f"지금 색인({meta.get('model')})과 계산할 모델({model})이 달라 전부 다시 만든다", flush=True)
        old = {}
    vecs: list = [old.get(k) if k in old else old.get(("id", r["id"])) for k, r in zip(keys, rows)]
    todo = [i for i, v in enumerate(vecs) if v is None]
    print(f"조각 {len(rows)}개 — 재사용 {len(rows) - len(todo)}, 새로 계산 {len(todo)} ({model}"
          f"{' · 서빙 장비' if remote_model else ' · 이 장비'})", flush=True)

    if todo:
        if remote_model:
            from zzaimy.app.embed_search import remote_vectors

            for s in range(0, len(todo), BATCH):
                part = todo[s:s + BATCH]
                got = remote_vectors([texts[i] for i in part], timeout=120)
                if got is None:
                    raise SystemExit("임베딩 서비스 계산 실패 — 색인을 바꾸지 않고 멈춘다")
                for i, v in zip(part, got):
                    vecs[i] = v
                if (s // BATCH) % 20 == 0:
                    print(f"  {min(s + BATCH, len(todo))}/{len(todo)}", flush=True)
        else:
            from sentence_transformers import SentenceTransformer

            enc = SentenceTransformer(os.environ.get("ZZAIMY_EMBED_MODEL", "nlpai-lab/KURE-v1"),
                                      device=os.environ.get("ZZAIMY_EMBED_DEVICE", "cpu"))
            got = enc.encode([texts[i] for i in todo], batch_size=16, show_progress_bar=False, normalize_embeddings=True)
            for i, v in zip(todo, got):
                vecs[i] = v

    mat = np.asarray(vecs, dtype="float32") if rows else np.zeros((0, 1024), dtype="float32")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.stem + ".tmp.npz")
    np.savez_compressed(tmp, ids=np.array([r["id"] for r in rows]), keys=np.array(keys), vectors=mat)
    tmp.replace(OUT)            # 읽는 쪽(앱)이 반쯤 쓴 파일을 보지 않게 다 쓴 뒤 바꿔 끼운다
    META.write_text(json.dumps({"model": model, "n_chunks": len(rows), "dim": int(mat.shape[1]),
                                "device": "serving" if remote_model else os.environ.get("ZZAIMY_EMBED_DEVICE", "cpu"),
                                "reused": len(rows) - len(todo),
                                # 이 색인을 만든 조각 지문 — 앱이 지금 조각과 견주어 바뀌면 다시 만든다(app/reindex_state)
                                "chunks_sig": f"{len(rows)}|{max((r['id'] for r in rows), default=0)}|{sum(r['id'] for r in rows)}"}, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {OUT} ({mat.shape})", flush=True)

    if not len(mat):
        return
    from zzaimy.app.embed_search import remote_vectors

    for q in PROBES:
        qv = remote_vectors([q]) if remote_model else None
        if qv is None:
            break
        sims = mat @ qv[0]
        print(f"\n[프로브] {q}", flush=True)
        for idx in sims.argsort()[-3:][::-1]:
            r = rows[int(idx)]
            print(f"  {sims[idx]:.3f} {r['reg_title'][:20]} | {r['heading'][:40]}", flush=True)


if __name__ == "__main__":
    main()
