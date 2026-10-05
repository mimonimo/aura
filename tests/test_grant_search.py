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


def test_short_hit_expands_with_following_chunks(tmp_path):
    """제목만 걸린 짧은 조각은 같은 문서의 뒤 조각을 이어 본문까지 건넨다."""
    db = Database(tmp_path / "t.db")
    d = db.add_document("계획서.hwp", "x", doc_type="grant", sector="grant")
    db.replace_doc_chunks(d, [{"kind": "text", "content": "2. 추진 일정"}, {"kind": "text", "content": "3월 공고, 4월 선정"},
                              {"kind": "text", "content": "5월 협약"}])
    first = min(db.list_doc_chunks(d), key=lambda c: c["seq"])
    out = grant_search.expand(db, {"doc_id": d, "seq": first["seq"], "content": "2. 추진 일정"})
    assert out.startswith("2. 추진 일정") and "4월 선정" in out and "5월 협약" in out


def test_quoted_phrase_first_and_per_doc_cap():
    cs = [{"id": 1, "doc_id": 9, "content": "예산 총괄"}, {"id": 2, "doc_id": 9, "content": "다른 내용"},
          {"id": 3, "doc_id": 9, "content": "또 다른"}, {"id": 4, "doc_id": 8, "content": "사회맞춤형 기자재 및 장비 구축 계획 표"}]
    got = grant_search.rerank_hits("LINC+ 계획서의 「사회맞춤형 기자재 및 장비 구축 계획」 내용", cs)
    assert got[0]["id"] == 4 and [c["id"] for c in got[1:]] == [1, 2, 3]
    assert grant_search.rerank_hits("질문", cs) == cs


def test_expand_many_matches_single_expand(tmp_path):
    db = Database(tmp_path / "t.db")
    d = db.add_document("계획서.hwp", "x", doc_type="grant", sector="grant")
    db.replace_doc_chunks(d, [{"kind": "text", "content": "2. 추진 일정"}, {"kind": "text", "content": "3월 공고"},
                              {"kind": "text", "content": "5월 협약"}])
    first = min(db.list_doc_chunks(d), key=lambda c: c["seq"])
    one = grant_search.expand(db, {"doc_id": d, "seq": first["seq"], "content": "2. 추진 일정"})
    c = {"doc_id": d, "seq": first["seq"], "content": "2. 추진 일정"}
    grant_search.expand_many(db, [c])
    assert c["content"] == one


def test_section_hits_use_graph_section_titles_in_scope(tmp_path):
    from zzaimy.graph import kg_store
    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    ds = []
    for name in ("a.hwp", "b.hwp", "c.hwp"):
        d = db.add_document(name, "x", doc_type="grant", sector="grant")
        db.replace_doc_chunks(d, [{"kind": "text", "content": "표지"}, {"kind": "text", "content": "1. 행사 개요"},
                                  {"kind": "text", "content": "일시 장소"}])
        ds.append(d)
    with db._conn() as c:
        for d in ds:
            kg_store.put_node(c, f"doc:{d}:sec:1", "section", "1. 행사 개요", {"chunks": [1, 2]}, d)
            kg_store.put_node(c, f"doc:{d}:sec:2", "section", "2. 행사 개요에 따른 세부 추진 계획과 예산 집행 방안", {"chunks": [2]}, d)
    got = grant_search.section_hits(db, "LINC 2023 실적보고서의 「행사 개요」 내용", {ds[0], ds[1]})
    ids = {x["id"]: x for d in ds for x in db.list_doc_chunks(d)}
    assert {ids[i]["doc_id"] for i in got} == {ds[0], ds[1]} and all(ids[i]["seq"] == 1 for i in got)
    assert grant_search.section_hits(db, "행사 개요", {ds[0]}) == []
