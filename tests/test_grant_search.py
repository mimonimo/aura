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


def test_increment_removes_and_adds_in_one_array_and_checks_without_reloading(tmp_path, monkeypatch):
    """10/4 VM OOM — 색인 갱신이 5GB 사본을 여러 벌 만들던 것을 한 배열로. 결과는 같아야 한다."""
    import numpy as np
    from zzaimy.app import grant_search as gs
    idx = tmp_path / "grant_embeddings.npz"
    monkeypatch.setattr(gs, "INDEX", idx)
    monkeypatch.setattr(gs, "PREV", idx.with_suffix(".prev.npz"))
    gs._cache.update(mtime=None, ids=None, vecs=None)
    db = Database(tmp_path / "t.db")
    did = db.add_document("계획서.hwp", "x", doc_type="grant", sector="grant")
    db.update_document(did, status="reviewed")
    db.replace_doc_chunks(did, [{"kind": "text", "content": f"조각 {i}"} for i in range(3)])
    cids = sorted(c["id"] for c in db.list_doc_chunks(did))
    gone = max(cids) + 100                                  # 색인에만 있고 문서함에서는 지워진 조각
    np.savez(idx, ids=np.array([cids[0], gone], dtype=np.int64),
             vectors=np.array([[1, 1], [9, 9]], dtype=np.float32))
    monkeypatch.setattr(gs, "_encode_batch", lambda texts: [[float(len(t)), 0.0] for t in texts])
    got = gs.build_increment(db, batch=1)
    assert (got["added"], got["removed"], got["total"]) == (2, 1, 3)
    ids, vecs = gs._read(idx)
    assert sorted(ids) == cids and vecs.shape == (3, 2)
    assert list(vecs[list(ids).index(cids[0])]) == [1.0, 1.0]    # 남긴 조각의 벡터는 그대로
    assert gs._check(idx) == 3


def test_check_rejects_truncated_vectors(tmp_path):
    import numpy as np
    from zzaimy.app import grant_search as gs
    p = tmp_path / "x.npz"
    np.savez(p, ids=np.arange(4, dtype=np.int64), vectors=np.ones((3, 2), dtype=np.float32))
    try:
        gs._check(p)
        raise AssertionError("모양이 다른데 통과")
    except ValueError:
        pass


def test_index_path_matches_access_and_scope(tmp_path, monkeypatch):
    """어휘 색인(grant_lex)을 채운 뒤에는 조각 전체를 읽지 않고 색인으로 — 열람 범위·사업 범위는 그대로 지킨다."""
    from zzaimy.app import grant_lex
    monkeypatch.setattr(grant_search, "INDEX", tmp_path / "none.npz")
    db = Database(tmp_path / "t.db")
    a = db.add_document("LINC3.0 실적보고서.hwp", "x", doc_type="grant", sector="grant")
    b = db.add_document("비공개 계획서.hwp", "x", doc_type="grant", sector="grant")
    c = db.add_document("RISE 계획서.hwp", "x", doc_type="grant", sector="grant")
    for did in (a, b, c):
        db.update_document(did, status="reviewed")
    db.replace_doc_chunks(a, [{"kind": "text", "content": "가족회사 운영 실적과 산학협력 기술지도 성과를 정리하였다."}])
    db.replace_doc_chunks(b, [{"kind": "text", "content": "가족회사 운영 계획 비공개 문서이다."}])
    db.replace_doc_chunks(c, [{"kind": "text", "content": "가족회사 협의회 운영 계획이다."}])
    with db._conn() as conn:
        conn.execute("UPDATE documents SET access_level='owner', owner='other' WHERE id=?", (b,))
    got = grant_lex.sync(db)
    assert got["added"] == 3 and grant_lex.coverage(db) == (3, 3)
    hits = grant_search.search(db, "가족회사 운영 실적은?", k=5, user="zzaimy")
    docs = {h["doc_id"] for h in hits["hits"]}
    assert a in docs and b not in docs
    assert any("색인" in s for s in hits["steps"])
    assert set(grant_lex.rank(db, frozenset({"가족회사", "운영"}), {c}, "zzaimy")) <= {
        x["id"] for x in db.list_doc_chunks(c)}
    db.replace_doc_chunks(c, [])                            # 지워진 조각은 색인에서도
    assert grant_lex.sync(db)["removed"] == 1
