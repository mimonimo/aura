from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import project_refs


def setup_browser(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    mine = db.create_project("grant", "2026 지역혁신 사업", owner="zzaimy")
    past = db.create_project("grant", "2025 지역혁신 사업", owner="zzdev", archived=True, program="program:rise|2025")
    doc = db.add_document("계획서.hwp", "dgx://앵커/계획서.hwp", doc_type="grant", project_id=past)
    client = TestClient(app)
    assert _login(client, "zzaimy", "boot-pass-1")
    return client, db, mine, past, doc


def test_browser_filters_and_reference_preserve_original(tmp_path):
    client, db, mine, past, doc = setup_browser(tmp_path)
    page = client.get(f"/projects/archived?for_project={mine}&year=2025")
    assert page.status_code == 200
    assert 'id="browserStatus"' in page.text and 'id="browserYear"' in page.text
    assert 'id="browserUnit"' in page.text and 'role="search"' in page.text
    assert 'name="for_project"' in page.text and '참조 연결' in page.text
    assert '/unarchive' not in page.text
    before = db.get_project(past)
    assert client.post(f"/project/{mine}/refs", data={"ref_project_id": past}, follow_redirects=False).status_code == 303
    after = db.get_project(past)
    assert (after["owner"], after["archived"]) == (before["owner"], before["archived"])
    assert db.get_document(doc)["project_id"] == past
    assert "연결됨" in client.get(f"/projects/archived?for_project={mine}").text
    assert "조건에 맞는 프로젝트가 없습니다" in client.get('/projects/archived?q=찾을수없는항목').text
    assert "2026 지역혁신 사업" in client.get('/projects/archived?status=active').text


def test_browser_rejects_foreign_target_and_hidden_reference(tmp_path):
    client, db, mine, past, doc = setup_browser(tmp_path)
    foreign = db.create_project("grant", "비공개 업무", owner="other")
    assert client.get(f"/projects/archived?for_project={foreign}").status_code == 404
    with db._conn() as conn:
        conn.execute("UPDATE documents SET access_level='owner', owner='other' WHERE id=?", (doc,))
    assert "2025 지역혁신 사업" not in client.get('/projects/archived').text
    assert client.post(f"/project/{mine}/refs", data={"ref_project_id": past}, follow_redirects=False).status_code == 404
    assert project_refs.list_refs(db, mine) == []
