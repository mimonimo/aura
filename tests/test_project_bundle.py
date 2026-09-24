"""문서 묶음 접수 — 공고·기본계획·지침은 프로젝트 기준 문서로, 계획서·양식은 접수 문서로, 이름은 묶음에서 뽑는다."""

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app.main import create_app
from zzaimy.ingest import gdrive_files


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return app, TestClient(app)


def _files():
    return [
        ("file", ("2026학년도 AID 전환 중점 전문대학 지원사업 기본계획.hwp", b"hwp-bytes", "application/octet-stream")),
        ("file", ("[붙임2] 사업계획서 양식.hwp", b"hwp-form", "application/octet-stream")),
        ("file", ("붙임3. 사업비 집행 지침.pdf", b"%PDF-1.4", "application/pdf")),
        ("file", ("예산 편성표.xlsx", b"xlsx", "application/octet-stream")),
        ("file", ("발표자료.pptx", b"pptx", "application/octet-stream")),
    ]


def test_bundle_creates_project_and_splits_criteria_from_intake(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    r = c.post("/projects/bundle", data={"sector": "grant"}, files=_files(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/project/")
    pid = int(r.headers["location"].split("/project/")[1].split("?")[0])
    project = db.get_project(pid)
    assert project["name"] == "2026학년도 AID 전환 중점 전문대학 지원사업"
    crit = {db.get_document(i)["filename"] for i in db.get_project_criteria_ids(pid)}
    assert crit == {"2026학년도 AID 전환 중점 전문대학 지원사업 기본계획.hwp", "붙임3. 사업비 집행 지침.pdf"}
    intake = {d["filename"] for d in db.list_documents("grant", project_id=pid)}
    assert intake == {"[붙임2] 사업계획서 양식.hwp", "예산 편성표.xlsx", "발표자료.pptx"}
    for d in db.list_documents("grant", project_id=pid):
        assert "반입" in d["stored_path"] and str(pid) in d["stored_path"] or "반입" in d["stored_path"]
    page = c.get(f"/project/{pid}?bundle=2+3").text
    assert "묶음을 접수했습니다" in page and "예산 편성표.xlsx" in page


def test_bundle_with_explicit_name_and_added_to_existing_project(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    r = c.post("/projects/bundle", data={"sector": "grant", "name": "AID 전환"},
               files=_files()[:1], follow_redirects=False)
    pid = int(r.headers["location"].split("/project/")[1].split("?")[0])
    assert db.get_project(pid)["name"] == "AID 전환"
    r2 = c.post(f"/project/{pid}/bundle", files=_files()[3:], follow_redirects=False)
    assert r2.status_code == 303
    assert len(db.list_documents("grant", project_id=pid)) == 2
    assert c.post("/projects/bundle", data={"sector": "grant"}, follow_redirects=False).status_code == 400


def test_doc_view_falls_back_to_platform_page_without_google(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    did = db.add_document(filename="a.xlsx", stored_path=str(tmp_path / "a.xlsx"), doc_type="grant")
    r = c.get(f"/doc/{did}/view", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/doc/{did}?view=local"


def test_bytes_for_view_converts_office_and_restored_hwp(tmp_path):
    app, _ = _client(tmp_path)
    db = app.state.db
    x = tmp_path / "b.xlsx"; x.write_bytes(b"x")
    did = db.add_document(filename="b.xlsx", stored_path=str(x), doc_type="grant")
    data, name, mime, target = gdrive_files.bytes_for_view(db, db.get_document(did))
    assert name == "b.xlsx" and target.endswith("spreadsheet")
    h = tmp_path / "c.hwp"; h.write_bytes(b"h")
    hid = db.add_document(filename="c.hwp", stored_path=str(h), doc_type="grant")
    assert gdrive_files.bytes_for_view(db, db.get_document(hid))[3] == ""          # 추출 전 — 원본 그대로
    db.replace_doc_chunks(hid, [{"kind": "heading", "content": "제목", "page_no": 1},
                               {"kind": "text", "content": "본문 한 줄", "page_no": 1}])
    data, name, mime, target = gdrive_files.bytes_for_view(db, db.get_document(hid))
    assert name == "c.docx" and target.endswith("document") and data[:2] == b"PK"
    assert gdrive_files.view_url("X", "application/vnd.google-apps.spreadsheet").startswith("https://docs.google.com/spreadsheets/")


def test_chat_documents_api_lists_attachments_criteria_and_intake(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    r = c.post("/projects/bundle", data={"sector": "grant"}, files=_files()[:2], follow_redirects=False)
    pid = int(r.headers["location"].split("/project/")[1].split("?")[0])
    r2 = c.post("/chat/send", data={"question": "검토해 줘", "project_id": str(pid)},
                files={"attachment": ("메모.pdf", b"%PDF-1.4 memo", "application/pdf")}, follow_redirects=False)
    sid = int(r2.headers["location"].rsplit("/", 1)[-1])
    data = c.get(f"/api/chat/{sid}/documents").json()
    assert data["project"] == "2026학년도 AID 전환 중점 전문대학 지원사업"
    groups = {(d["group"], d["name"]) for d in data["documents"]}
    assert ("첨부", "메모") in groups and ("기준", "2026학년도 AID 전환 중점 전문대학 지원사업 기본계획") in groups
    assert any(g == "접수" for g, _ in groups)
    assert all(d["url"].endswith("/view") for d in data["documents"])
    assert c.get("/api/chat/9999/documents").status_code == 404


def test_doc_google_api_without_account_says_so(tmp_path):
    app, c = _client(tmp_path)
    did = app.state.db.add_document(filename="a.xlsx", stored_path=str(tmp_path / "a.xlsx"), doc_type="grant")
    r = c.get(f"/api/doc/{did}/google")
    assert r.status_code == 400 and "허용 계정" in r.json()["detail"]
    assert gdrive_files.embed_url("X", "application/vnd.google-apps.presentation") == "https://docs.google.com/presentation/d/X/edit?rm=minimal"
    assert gdrive_files.embed_url("X", "application/pdf") == "https://drive.google.com/file/d/X/preview"
