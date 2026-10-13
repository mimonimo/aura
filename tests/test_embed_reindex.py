"""규정 조각 색인 갱신: 앱은 색인 파일이 바뀌면 재시작 없이 다시 읽고, 52 는 글이 그대로인 조각을 다시 계산하지 않는다."""
import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace as C

import numpy as np

from zzaimy.app import embed_search


def _write(path: Path, ids, dim=4):
    vecs = np.eye(len(ids), dim, dtype="float32")
    np.savez_compressed(path, ids=np.array(ids), vectors=vecs)


def test_index_reloads_when_file_changes(tmp_path, monkeypatch):
    npz = tmp_path / "chunk_embeddings.npz"
    _write(npz, [1, 2])
    monkeypatch.setattr(embed_search, "INDEX_PATH", npz)
    monkeypatch.setenv("ZZAIMY_EMBED_URL", "http://127.0.0.1:9/embed")     # 질의는 서비스 몫 — 모델을 올리지 않는다
    idx = embed_search.EmbedIndex()
    assert idx._load() and list(idx._ids) == [1, 2]
    _write(npz, [1, 2, 3])
    os.utime(npz, (time.time() + 5, time.time() + 5))
    assert idx._load() and list(idx._ids) == [1, 2, 3]


def _load_52():
    spec = importlib.util.spec_from_file_location("s52", Path(__file__).parents[1] / "scripts" / "52_embed_chunks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_embed_chunks_reuses_unchanged_text(tmp_path, monkeypatch):
    from zzaimy.app.db import Database

    s52 = _load_52()
    db = Database(tmp_path / "p.db")
    d = db.add_document("규정.hwpx", "x", doc_type="regulation")
    db.add_regulation_chunks(d, "학칙", [C(heading="제1조", content="목적"), C(heading="제2조", content="정의")])
    npz, meta = tmp_path / "chunk_embeddings.npz", tmp_path / "chunk_embeddings.meta.json"
    monkeypatch.setattr(s52, "DB", str(tmp_path / "p.db"))
    monkeypatch.setattr(s52, "OUT", npz)
    monkeypatch.setattr(s52, "META", meta)
    monkeypatch.setattr(s52, "served_model", lambda: "embed-x")
    calls = []

    def fake_remote(texts, timeout=None):
        calls.append(len(texts))
        return np.ones((len(texts), 4), dtype="float32") / 2

    monkeypatch.setattr(embed_search, "remote_vectors", fake_remote)
    monkeypatch.setattr("sys.argv", ["52"])
    s52.main()
    first = calls[0]
    assert first == 2 and json.loads(meta.read_text())["model"] == "embed-x"
    calls.clear()
    d2 = db.add_document("지침.hwpx", "x", doc_type="regulation")
    db.add_regulation_chunks(d2, "지침", [C(heading="제1조", content="새 조문")])
    s52.main()
    assert calls[0] == 1                                     # 새 조각 하나만 계산(뒤는 프로브 질의)
    assert json.loads(meta.read_text())["n_chunks"] == 3


def test_serving_status_flags_fallback_model_mismatch(monkeypatch):
    from zzaimy.app import search_serving

    monkeypatch.setenv("ZZAIMY_EMBED_URL", "http://x/embed")
    monkeypatch.delenv("ZZAIMY_RERANK_URL", raising=False)
    monkeypatch.setattr(search_serving, "_health", lambda url: {"ok": True, "model": "/models/zzaimy-embed-v2", "detail": ""})
    search_serving.clear_cache()
    monkeypatch.setattr(embed_search, "MODEL_NAME", "nlpai-lab/KURE-v1")
    row = next(p for p in search_serving.status() if p["key"] == "embed")
    assert row["fallback_same"] is False and "어긋납니다" in row["detail"]
    search_serving.clear_cache()
    monkeypatch.setattr(embed_search, "MODEL_NAME", "/home/x/models/zzaimy-embed-v2")
    row = next(p for p in search_serving.status() if p["key"] == "embed")
    assert row["fallback_same"] is True and row["detail"] == "비상용 VM zzaimy-embed-v2"
