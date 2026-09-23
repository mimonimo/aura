"""데이터 체계(ADR-0031) — 경로는 paths 한 곳, 이관 139, 내보내기 140."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from zzaimy.app import paths
from zzaimy.app.db import Database


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_paths_follow_two_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("ZZAIMY_DATA_DIR", str(tmp_path / "p"))
    monkeypatch.setenv("ZZAIMY_TRAIN_DIR", str(tmp_path / "t"))
    monkeypatch.delenv("ZZAIMY_EMBED_INDEX", raising=False)
    assert paths.index_npz() == tmp_path / "p" / "knowledge" / "index" / "chunk_embeddings.npz"
    assert paths.index_meta().name == "chunk_embeddings.meta.json"
    assert paths.eval_dir() == tmp_path / "p" / "knowledge" / "eval"
    assert paths.restored_dir() == tmp_path / "p" / "cache" / "restored"
    assert paths.writer_baseline() == tmp_path / "t" / "baselines" / "writer" / "baseline.json"
    assert paths.train_models("writer", "v1") == tmp_path / "t" / "models" / "writer" / "v1"
    monkeypatch.setenv("ZZAIMY_EMBED_INDEX", str(tmp_path / "old.npz"))
    assert paths.index_npz() == tmp_path / "old.npz" and paths.index_npz(tmp_path / "p") != tmp_path / "old.npz"


def test_migration_moves_knowledge_cache_and_baseline(tmp_path, monkeypatch):
    base, train, repo_eval = tmp_path / "platform", tmp_path / "train", tmp_path / "eval"
    base.mkdir(); repo_eval.mkdir()
    np.savez(base / "chunk_embeddings.npz", ids=np.array([1]), vectors=np.zeros((1, 4)))
    (base / "chunk_embeddings.meta.json").write_text("{}")
    (base / "eval").mkdir(); (base / "eval" / "retrieval-latest.json").write_text("{}")
    (base / "corpus_pilot.db").write_bytes(b"x")
    (base / "pagecache").mkdir(); (base / "pagecache" / "1-1.png").write_bytes(b"p")
    (repo_eval / "baseline.json").write_text("{}"); (repo_eval / "llm-rerank-x.json").write_text("{}")
    mig = _load("139_layout_knowledge")
    monkeypatch.setattr(sys, "argv", ["139", "--data", str(base), "--train", str(train), "--repo-eval", str(repo_eval), "--apply"])
    assert mig.main() == 0
    assert (base / "knowledge" / "index" / "chunk_embeddings.npz").exists()
    assert (base / "knowledge" / "eval" / "retrieval-latest.json").exists() and (base / "knowledge" / "eval" / "llm-rerank-x.json").exists()
    assert (base / "knowledge" / "corpus_pilot" / "corpus_pilot.db").exists()
    assert (base / "cache" / "pagecache" / "1-1.png").exists() and not (base / "pagecache").exists()
    assert (train / "baselines" / "writer" / "baseline.json").exists() and not repo_eval.exists()


def test_export_bundles_public_documents_chunks_graph_and_vectors(tmp_path):
    base = tmp_path / "platform"; base.mkdir()
    db = Database(base / "platform.db")
    pub = db.add_document("공고.pdf", "x", doc_type="regulation")
    sec = db.add_document("접수.pdf", "x", doc_type="grant", dept="학생처", access_level="dept")
    db.update_document(pub, status="reviewed", masked_text="공개 본문"); db.update_document(sec, status="reviewed", masked_text="부서 본문")
    from zzaimy.app.regulations import RegulationChunk
    db.add_regulation_chunks(pub, "공고", [RegulationChunk(heading="제1조", content="공개 조각 내용입니다.")])
    db.add_regulation_chunks(sec, "접수", [RegulationChunk(heading="1.", content="부서 제한 조각입니다.")], dept="학생처", access_level="dept")
    ids = [c["id"] for c in db.list_regulation_chunks()]
    (base / "knowledge" / "index").mkdir(parents=True)
    np.savez(paths.index_npz(base), ids=np.array(ids), vectors=np.ones((len(ids), 4)))
    paths.index_meta(base).write_text(json.dumps({"model": "zzaimy-embed-v2"}))
    exp = _load("140_export_knowledge")
    out = base / "knowledge" / "exports" / "t"
    m = exp.export(base / "platform.db", out, ("public",), base=base, make_zip=True)
    assert m["documents"] == 1 and m["chunks"] == 1 and m["embeddings"] == 1 and m["embedding_model"] == "zzaimy-embed-v2"
    docs = [json.loads(ln) for ln in (out / "documents.jsonl").read_text().splitlines()]
    assert docs[0]["filename"] == "공고.pdf" and "부서" not in (out / "chunks.jsonl").read_text()
    assert (out.parent / "t.zip").exists()
    m2 = exp.export(base / "platform.db", base / "knowledge" / "exports" / "u", ("public", "dept"), base=base)
    assert m2["documents"] == 2 and m2["chunks"] == 2
