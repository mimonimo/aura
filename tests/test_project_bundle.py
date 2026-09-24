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
    assert "문서를 추가했습니다" in page and "예산 편성표.xlsx" in page


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


def test_doc_view_falls_back_to_platform_page_without_google(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, user, dept=None: "")
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


def test_doc_google_api_without_account_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, user, dept=None: "")   # 운영 VM 의 실제 토큰과 무관하게
    app, c = _client(tmp_path)
    did = app.state.db.add_document(filename="a.xlsx", stored_path=str(tmp_path / "a.xlsx"), doc_type="grant")
    r = c.get(f"/api/doc/{did}/google")
    assert r.status_code == 400 and "허용 계정" in r.json()["detail"]
    assert gdrive_files.embed_url("X", "application/vnd.google-apps.presentation") == "https://docs.google.com/presentation/d/X/edit?rm=minimal"
    assert gdrive_files.embed_url("X", "application/pdf") == "https://drive.google.com/file/d/X/preview"


def test_duplicate_hwp_view_uses_original_chunks(tmp_path):
    app, _ = _client(tmp_path)
    db = app.state.db
    o = tmp_path / "o.hwp"; o.write_bytes(b"h")
    orig = db.add_document(filename="o.hwp", stored_path=str(o), doc_type="grant")
    db.replace_doc_chunks(orig, [{"kind": "text", "content": "원본 본문", "page_no": 1}])
    d = tmp_path / "d.hwp"; d.write_bytes(b"h")
    dup = db.add_document(filename="d.hwp", stored_path=str(d), doc_type="grant")
    db.set_document_family(dup, "x", version_of=orig)
    data, name, mime, target = gdrive_files.bytes_for_view(db, db.get_document(dup))
    assert name == "d.docx" and data[:2] == b"PK" and target.endswith("document")


def test_titles_from_web_downloads_get_spaces_back():
    from zzaimy.app import storage

    assert storage.title_of("2026학년도+AID+전환+중점+전문대학+지원사업+공고문.pdf") == "2026학년도 AID 전환 중점 전문대학 지원사업 공고문"
    assert storage.title_of("2._2026-1학기_개설과목_관리_중_AI관련_교과목(01.27).xlsx") == "2. 2026-1학기 개설과목 관리 중 AI관련 교과목(01.27)"
    assert storage.title_of("2026년 AID 사업계획서 Ver.3.3 260401_2330.hwp") == "2026년 AID 사업계획서 Ver.3.3 260401_2330"


def test_bundle_name_from_web_download_names(tmp_path):
    app, c = _client(tmp_path)
    files = [("file", ("2026학년도+AID+전환+중점+전문대학+지원사업+공고문.pdf", b"%PDF-1.4", "application/pdf")),
             ("file", ("2026년 AID 전환 중점 전문대학 지원사업 사업계획서 Ver.3.3 260401_2330.hwp", b"h", "application/octet-stream"))]
    r = c.post("/projects/bundle", data={"sector": "grant"}, files=files, follow_redirects=False)
    pid = int(r.headers["location"].split("/project/")[1].split("?")[0])
    assert app.state.db.get_project(pid)["name"] == "2026학년도 AID 전환 중점 전문대학 지원사업"


def test_rightmost_title_cue_decides_kind():
    from zzaimy.app.doc_routing import guess_kind

    assert guess_kind("AID선정평가 지표정의서 및 평가편람(ver5).hwpx", "")[0] == "guideline"
    assert guess_kind("AID선정평가사업계획서 작성서식(ver5).hwpx", "")[0] == "form"
    assert guess_kind("규정 개정 신청서.hwp", "")[0] == "form"
    assert guess_kind("2026학년도 지원사업 기본계획.pdf", "")[0] == "plan"


def test_project_doc_named_matches_title_words(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    r = c.post("/projects/bundle", data={"sector": "grant"}, files=_files()[:2], follow_redirects=False)
    pid = int(r.headers["location"].split("/project/")[1].split("?")[0])
    project = db.get_project(pid)
    f = app.state.project_doc_named
    assert f(project, 0, "사업계획서 양식으로 작성 시작하자")["filename"].endswith("사업계획서 양식.hwp")
    assert f(project, 0, "AID 전환 중점 전문대학 지원사업 기본계획을 독스로 열어 줘")["filename"].endswith("기본계획.hwp")
    assert f(project, 0, "기본계획을 독스로 열어 줘") is None          # 낱말 하나로는 지목이 아니다
    assert f(project, 0, "예산이 얼마야") is None


def test_working_on_a_project_document_links_its_docs_copy(tmp_path, monkeypatch):
    """"작성서식으로 작업하자" → 한글 양식을 독스로 바꿔 대화에 잇고, 이후 명령은 그 문서를 고친다."""
    from zzaimy.ingest import gdrive, gdrive_files

    app, c = _client(tmp_path)
    db = app.state.db
    r = c.post("/projects/bundle", data={"sector": "grant"}, files=_files()[:2], follow_redirects=False)
    pid = int(r.headers["location"].split("/project/")[1].split("?")[0])
    monkeypatch.setattr(gdrive, "list_accounts", lambda: ["staff@example.ac.kr"])
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, user, dept=None: "staff@example.ac.kr")
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda email: True)
    monkeypatch.setattr(gdrive_files, "project_folder_for", lambda *a, **k: "folderX")
    made = {}
    def fake_copy(db_, doc, email, folder, http=None):
        made["doc"] = doc["filename"]
        return {"id": "gdoc1", "mime": "application/vnd.google-apps.document", "url": "https://docs.google.com/document/d/gdoc1/edit"}
    monkeypatch.setattr(gdrive_files, "google_copy", fake_copy)
    monkeypatch.setattr(gdrive_files, "copy_document", lambda email, fid, title, folder=None, http=None:
                        {"id": "work1", "name": title, "mime": "application/vnd.google-apps.document", "url": "https://docs.google.com/document/d/work1/edit"})
    r = c.post("/chat/send", data={"question": "사업계획서 양식으로 작업하자", "project_id": str(pid)}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[-1])
    assert made["doc"].endswith("사업계획서 양식.hwp")
    import json
    assert json.loads(db.get_setting(f"chat_google_doc:{sid}", ""))["doc"] == "work1"      # 원본 변환본(gdoc1)이 아니라 복제본
    page = c.get(r.headers["location"]).text
    assert "복제본" in page and "원본 서식은 그대로" in page
    assert db.list_files(kind="google", session_id=sid)[0]["name"].startswith("[붙임2] 사업계획서 양식 작업본")


def test_project_doc_named_ignores_dates_and_versions(tmp_path):
    app, _ = _client(tmp_path)
    db = app.state.db
    pid = db.create_project("grant", "AID")
    db.add_document(filename="20260219-AID선정평가사업계획서 작성서식(ver5).hwpx", stored_path=str(tmp_path / "a.hwpx"), doc_type="grant", project_id=pid)
    db.add_document(filename="20260219-AID선정평가 지표정의서 및 평가편람(ver5).hwpx", stored_path=str(tmp_path / "b.hwpx"), doc_type="grant", project_id=pid)
    f = app.state.project_doc_named
    assert f(db.get_project(pid), 0, "AID선정평가사업계획서 작성서식으로 작업하자")["filename"].endswith("작성서식(ver5).hwpx")
    assert f(db.get_project(pid), 0, "지표정의서 및 평가편람을 독스로 열어 줘")["filename"].endswith("평가편람(ver5).hwpx")


def test_ambiguous_document_reference_asks_instead_of_guessing(tmp_path, monkeypatch):
    from zzaimy.ingest import gdrive, gdrive_files

    app, c = _client(tmp_path)
    db = app.state.db
    pid = db.create_project("grant", "AID")
    db.add_document(filename="사업계획서 작성서식 2025.hwpx", stored_path=str(tmp_path / "a.hwpx"), doc_type="grant", project_id=pid)
    db.add_document(filename="사업계획서 작성서식 2026.hwpx", stored_path=str(tmp_path / "b.hwpx"), doc_type="grant", project_id=pid)
    monkeypatch.setattr(gdrive, "list_accounts", lambda: ["staff@example.ac.kr"])
    r = c.post("/chat/send", data={"question": "사업계획서 작성서식으로 작업하자", "project_id": str(pid)}, follow_redirects=False)
    page = c.get(r.headers["location"]).text
    assert "어느 문서로 작업할지 분명하지 않습니다" in page and "작성서식 2025" in page and "작성서식 2026" in page
    assert not db.get_setting(f"chat_google_doc:{int(r.headers['location'].rsplit('/', 1)[-1])}", "")


def test_project_evidence_uses_intake_documents(tmp_path, monkeypatch):
    """양식을 채울 재료는 프로젝트의 접수 문서(합본·지난 계획서)에 있다 — 명령과 낱말이 겹치는 조각이 근거로 붙는다."""
    from zzaimy.app import gdocs_agent
    from zzaimy.ingest import gdrive
    import json

    app, c = _client(tmp_path)
    db = app.state.db
    pid = db.create_project("grant", "AID")
    did = db.add_document(filename="사업계획서 합본.pdf", stored_path=str(tmp_path / "h.pdf"), doc_type="grant", project_id=pid)
    db.update_document(did, status="reviewed")
    db.replace_doc_chunks(did, [{"kind": "text", "content": "대학의 AI·DX 교육여건 분석: 재학생 3,200명 가운데 AI 관련 교과목 이수자는 41%이며 산업체 수요 조사 결과 데이터 분석 인력이 부족하다.", "page_no": 12},
                               {"kind": "text", "content": "예산 편성은 인건비와 운영비로 나눈다.", "page_no": 80}])
    sid = db.create_chat_session("작성", project_id=pid, owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    seen = {}
    from zzaimy.app import chat_documents
    monkeypatch.setattr(chat_documents, "material", lambda db_, sid_, owner_: "[연결 문서] 사업계획서 작성서식 작업본")
    monkeypatch.setattr(gdrive, "list_accounts", lambda: ["staff@example.ac.kr"])
    def fake_run(db_, session_id, owner, command, link, *, client, data_dir, scrub=None, evidence=None, confirm=False, http=None):
        seen["evidence"] = evidence or []
        return "적용됨: 1곳", []
    monkeypatch.setattr(gdocs_agent, "run", fake_run)
    from zzaimy.generate import client as _gc
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: object())
    c.post("/chat/send", data={"question": "합본의 대학 AI·DX 교육여건 분석 내용으로 1.1 절을 채워 줘", "session_id": str(sid)}, follow_redirects=False)
    ev = seen["evidence"]
    assert ev and ev[0]["origin"] == "프로젝트 문서" and "교육여건" in ev[0]["content"] and ev[0]["reg_title"] == "사업계획서 합본"


def test_text_layer_cleans_bad_glyphs():
    from zzaimy.app.pipeline import _clean_glyphs

    assert _clean_glyphs("사업추진￾ 목표\r\n 항목") == "사업추진 목표\r\n· 항목"


def test_project_evidence_targets_named_document_and_skips_form_source(tmp_path, monkeypatch):
    from zzaimy.app import chat_documents, gdocs_agent
    from zzaimy.ingest import gdrive
    import json

    app, c = _client(tmp_path)
    db = app.state.db
    pid = db.create_project("grant", "AID")
    form = db.add_document(filename="사업계획서 작성서식.hwpx", stored_path=str(tmp_path / "f.hwpx"), doc_type="grant", project_id=pid)
    merged = db.add_document(filename="사업계획서 합본.pdf", stored_path=str(tmp_path / "h.pdf"), doc_type="grant", project_id=pid)
    for d in (form, merged):
        db.update_document(d, status="reviewed")
    db.replace_doc_chunks(form, [{"kind": "text", "content": "【작성방법】 대학의 AI·DX 교육여건 분석 결과를 기술하고 지역 산업 여건과 인력수요를 분석하라.", "page_no": 2}])
    db.replace_doc_chunks(merged, [{"kind": "text", "content": "대학의 AI·DX 교육여건 분석: 재학생 3,200명 가운데 AI 교과목 이수자 41%, 지역 산업 여건은 제조업 중심이며 인력수요 조사 결과 데이터 인력이 부족하다.", "page_no": 12}])
    sid = db.create_chat_session("작성", project_id=pid, owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "work1", "account": "staff@example.ac.kr"}))
    db.add_file("google", "https://docs.google.com/document/d/work1/edit", name="작업본", session_id=sid, doc_id=form)
    seen = {}
    monkeypatch.setattr(chat_documents, "material", lambda db_, sid_, owner_: "[연결 문서] 작업본")
    monkeypatch.setattr(gdrive, "list_accounts", lambda: ["staff@example.ac.kr"])
    def fake_run(db_, session_id, owner, command, link, *, client, data_dir, scrub=None, evidence=None, confirm=False, http=None):
        seen["evidence"] = evidence or []
        return "적용됨: 1곳", []
    monkeypatch.setattr(gdocs_agent, "run", fake_run)
    from zzaimy.generate import client as _gc
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: object())
    c.post("/chat/send", data={"question": "대학의 AI·DX 교육여건 분석 절을 채워 줘", "session_id": str(sid)}, follow_redirects=False)
    assert all(e["doc_id"] != form for e in seen["evidence"]) and any(e["doc_id"] == merged for e in seen["evidence"])
    c.post("/chat/send", data={"question": "사업계획서 합본의 교육여건 분석 내용으로 절을 채워 줘", "session_id": str(sid)}, follow_redirects=False)
    assert [e["doc_id"] for e in seen["evidence"]] == [merged]
