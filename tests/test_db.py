

def test_referencing_documents_reverse_links(tmp_path):
    """기준 문서에서 그 기준을 참조한 접수 문서를 역으로 찾는다."""
    import json

    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    reg = db.add_document("규정.pdf", "r.pdf", doc_type="regulation")
    d1 = db.add_document("신청서.pdf", "a.pdf", doc_type="grant",
                         related_criteria_id=reg)
    d2 = db.add_document("보고서.pdf", "b.pdf", doc_type="grant")
    db.update_document(d2, suggested_criteria=json.dumps(
        [{"id": reg, "title": "규정"}], ensure_ascii=False))
    pid = db.create_project("grant", "사업A")
    db.add_project_criteria(pid, [reg])
    d3 = db.add_document("프로젝트문서.pdf", "c.pdf", doc_type="grant",
                         project_id=pid)
    other = db.add_document("무관.pdf", "d.pdf", doc_type="grant")

    ids = {d["id"] for d in db.referencing_documents(reg)}
    assert ids == {d1, d2, d3}
    assert other not in ids
