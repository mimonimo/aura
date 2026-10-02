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
