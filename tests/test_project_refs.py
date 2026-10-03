"""관련 보관 사업 참조(C-192) — 참조는 보관 해제·소유권 변경·문서 이동이 아니다. 문서 수는 열람 범위 안에서만."""
from zzaimy.app import project_refs
from zzaimy.app.db import Database


def _setup(tmp_path):
    db = Database(tmp_path / "t.db")
    mine = db.create_project("grant", "2026학년도 AID 전환 중점 전문대학 지원사업", owner="kim")
    past = db.create_project("grant", "2025년 AID 전환 중점 전문대학 지원사업", owner="zzdev", archived=True, program="program:aid")
    other = db.create_project("grant", "2023년 RISE사업", owner="zzdev", archived=True, program="program:rise")
    for i in range(3):
        db.add_document(f"p{i}.hwp", f"dgx://p/{i}.hwp", doc_type="grant", project_id=past)
    hidden = db.add_document("h.hwp", "dgx://p/h.hwp", doc_type="grant", project_id=past)
    with db._conn() as conn:
        conn.execute("UPDATE documents SET access_level = 'owner', owner = 'lee' WHERE id = ?", (hidden,))
    db.add_document("r.hwp", "dgx://r/r.hwp", doc_type="grant", project_id=other)
    return db, mine, past, other


def test_candidates_by_name_overlap_and_visible_counts(tmp_path):
    db, mine, past, other = _setup(tmp_path)
    scope = {"dept": None, "user": "kim", "role": "staff"}
    got = project_refs.candidates(db, db.get_project(mine), "", scope)
    assert [c["id"] for c in got] == [past]                     # RISE 는 이름이 겹치지 않는다
    assert got[0]["n_docs"] == 3                                # 남의 담당자 한정 문서는 세지 않는다
    assert [c["id"] for c in project_refs.candidates(db, db.get_project(mine), "RISE", scope)] == [other]


def test_link_keeps_archive_state_owner_and_documents(tmp_path):
    db, mine, past, other = _setup(tmp_path)
    before = [d["project_id"] for d in db.list_documents()]
    project_refs.link(db, mine, past, "이름 겹침", "kim")
    ref = db.get_project(past)
    assert ref["archived"] == 1 and ref["owner"] == "zzdev"
    assert [d["project_id"] for d in db.list_documents()] == before
    refs = project_refs.list_refs(db, mine, {"dept": None, "user": "kim", "role": "staff"})
    assert [(r["ref_project_id"], r["n_docs"]) for r in refs] == [(past, 3)]
    assert project_refs.candidates(db, db.get_project(mine), "", None) == []   # 이미 연결된 것은 후보에서 빠진다
    project_refs.unlink(db, mine, past)
    assert project_refs.list_refs(db, mine) == []


def test_project_page_shows_refs_and_link_route(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login

    app = _app(tmp_path)
    db = app.state.db
    mine = db.create_project("grant", "2026학년도 AID 전환 중점 전문대학 지원사업", owner="zzaimy")
    past = db.create_project("grant", "2025년 AID 전환 중점 전문대학 지원사업", owner="zzdev", archived=True, program="program:aid")
    db.add_document("p.hwp", "dgx://p/p.hwp", doc_type="grant", project_id=past)
    client = TestClient(app)
    assert _login(client, "zzaimy", "boot-pass-1")
    page = client.get(f"/project/{mine}")
    assert page.status_code == 200 and "관련 보관 사업" in page.text and "2025년 AID" in page.text
    assert client.post(f"/project/{mine}/refs", data={"ref_project_id": past, "reason": "이름 겹침"},
                       follow_redirects=False).status_code == 303
    assert db.get_project(past)["archived"] == 1
    assert client.post(f"/project/{mine}/refs", data={"ref_project_id": mine}, follow_redirects=False).status_code == 404


def test_new_project_gets_related_archived_refs(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login

    app = _app(tmp_path)
    db = app.state.db
    past = db.create_project("grant", "2025년 AID 전환 중점 전문대학 지원사업", owner="zzdev", archived=True, program="program:aid|2025")
    other = db.create_project("grant", "2023년 RISE사업", owner="zzdev", archived=True, program="program:rise|2023")
    db.add_document("p.hwp", "dgx://p/p.hwp", doc_type="grant", project_id=past)
    db.add_document("r.hwp", "dgx://r/r.hwp", doc_type="grant", project_id=other)
    client = TestClient(app)
    assert _login(client, "zzaimy", "boot-pass-1")
    r = client.post("/projects", data={"sector": "grant", "name": "2026학년도 AID 전환 중점 전문대학 지원사업"}, follow_redirects=False)
    pid = int(r.headers["location"].rsplit("/", 1)[-1])
    refs = project_refs.list_refs(db, pid)
    assert [x["ref_project_id"] for x in refs] == [past] and "자동" in refs[0]["reason"]
    assert db.get_project(past)["archived"] == 1 and db.get_project(past)["owner"] == "zzdev"
