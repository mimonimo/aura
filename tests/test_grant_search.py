from zzaimy.app import grant_search
from zzaimy.app.db import Database


def test_grant_search_lexical_with_access_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(grant_search, "INDEX", tmp_path / "none.npz")
    db = Database(tmp_path / "t.db")
    a = db.add_document("LINC3.0 실적보고서.hwp", "x", doc_type="grant", sector="grant")
    b = db.add_document("비공개 계획서.hwp", "x", doc_type="grant", sector="grant")
    r = db.add_document("학칙.hwp", "x", doc_type="regulation")
    for did in (a, b, r):
        db.update_document(did, status="reviewed")
    db.replace_doc_chunks(a, [{"kind": "text", "content": "가족회사 운영 실적과 산학협력 기술지도 성과를 정리하였다."}])
    db.replace_doc_chunks(b, [{"kind": "text", "content": "가족회사 운영 계획 비공개 문서이다."}])
    db.replace_doc_chunks(r, [{"kind": "text", "content": "가족회사 규정 조항이다."}])
    with db._conn() as conn:
        conn.execute("UPDATE documents SET access_level='owner', owner='other' WHERE id=?", (b,))
    got = grant_search.search(db, "가족회사 운영 실적은?", k=5, user="zzaimy")
    docs = {h["doc_id"] for h in got["hits"]}
    assert a in docs and b not in docs and r not in docs          # 열람 범위·계열 분리
    assert any("근거 선택" in s for s in got["steps"])


def test_corrupt_index_falls_back_to_previous_and_writes_are_verified(tmp_path, monkeypatch):
    import numpy as np
    from zzaimy.app import grant_search as gs
    idx = tmp_path / "grant_embeddings.npz"
    monkeypatch.setattr(gs, "INDEX", idx)
    monkeypatch.setattr(gs, "PREV", idx.with_suffix(".prev.npz"))
    gs._cache.update(mtime=None, ids=None, vecs=None)
    np.savez_compressed(gs.PREV, ids=np.array([1, 2]), vectors=np.ones((2, 3), dtype=np.float32))
    idx.write_bytes(b"PK\x03\x04 broken")                  # 깨진 색인
    ids, vecs = gs._load()
    assert list(ids) == [1, 2] and vecs.shape == (2, 3)
    gs.PREV.unlink()
    gs._cache.update(mtime=None, ids=None, vecs=None)
    assert gs._load() == (None, None)                       # 정상본도 없으면 어휘 단독


def test_search_prefers_project_reference_documents(tmp_path, monkeypatch):
    from zzaimy.app import grant_search as gs
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    a = db.add_document("a.hwp", "dgx://a.hwp", doc_type="grant")
    b = db.add_document("b.hwp", "dgx://b.hwp", doc_type="grant")
    for d, text in ((a, "취업률 목표 70% 달성 계획"), (b, "취업률 목표 80% 달성 실적")):
        db.replace_doc_chunks(d, [{"seq": 0, "kind": "text", "content": text}])
        db.update_document(d, status="reviewed")
    monkeypatch.setattr(gs, "dense_ids", lambda q, allowed, top_k=60: [])
    got = gs.search(db, "취업률 목표", k=5, prefer_docs={b})
    assert [h["doc_id"] for h in got["hits"]] == [b]
    assert any("참조 보관 사업" in s for s in got["steps"])
