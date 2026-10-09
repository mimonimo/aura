"""구글 독스 문서 작업 — 가짜 독스 API 로 구조 읽기·절 삽입·치환·감사 기록·화면 경로를 검증한다. 구글에는 아무것도 보내지 않는다."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app.main import create_app
from zzaimy.app.db import Database
from zzaimy.ingest import gdocs, gdrive


def _para(start: int, text: str, style: str = "NORMAL_TEXT") -> dict:
    return {"startIndex": start, "endIndex": start + len(text),
            "paragraph": {"paragraphStyle": {"namedStyleType": style},
                          "elements": [{"textRun": {"content": text}}]}}


def test_outline_keeps_real_heading_and_tab_anchors():
    paragraph = _para(1, '1. 추진 배경\n', 'HEADING_1')
    paragraph['paragraph']['paragraphStyle']['headingId'] = 'h.real'
    document = {'tabs': [{'tabProperties': {'tabId': 't.real'}, 'documentTab': {'body': {'content': [paragraph, _para(20, '2. 추진 계획\n')]}}}]}
    sections = gdocs.outline(document)['sections']
    assert sections[0]['heading_id'] == 'h.real'
    assert sections[0]['tab_id'] == 't.real'
    assert sections[1]['heading_id'] is None


DOC = {"documentId": "docA", "title": "2026 사업계획서", "body": {"content": [
    {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
    _para(1, "2026 사업계획서\n", "TITLE"),
    _para(11, "1. 추진 배경\n", "HEADING_1"),
    _para(20, "지역 산업 수요가 늘고 있다.\n"),
    _para(36, "2. 추진 계획\n", "HEADING_1"),
    _para(45, "세부 과제를 둔다.\n"),
]}}


def fake_docs(calls: list):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.content.decode() if req.content else ""))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        if req.headers.get("Authorization") != "Bearer AT":
            return httpx.Response(401)
        if req.url.path == "/v1/documents/docA" and req.method == "GET":
            return httpx.Response(200, json=DOC)
        if req.url.path == "/v1/documents/docA:batchUpdate":
            body = json.loads(req.content)
            replies = [{"replaceAllText": {"occurrencesChanged": 2}} if "replaceAllText" in r else {} for r in body["requests"]]
            return httpx.Response(200, json={"documentId": "docA", "replies": replies})
        if req.url.path == "/v1/documents/missing":
            return httpx.Response(404)
        return httpx.Response(403)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def docs_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ZZAIMY_NAS_AUTO", "0")
    gdrive.set_client("abc.apps.googleusercontent.com", "s")
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps({"staff@example.ac.kr": {
        "refresh_token": "RT", "access_token": "", "expires_at": 0, "scopes": gdrive.SCOPES, "granted_at": "x"},
        "old@example.ac.kr": {"refresh_token": "RT", "access_token": "", "expires_at": 0,
                              "scopes": ["https://www.googleapis.com/auth/drive.readonly"], "granted_at": "x"}}))
    calls: list = []
    monkeypatch.setattr(gdrive, "_http", lambda: fake_docs(calls))
    return tmp_path, calls


def test_outline_reads_sections_and_text(docs_env):
    info = gdocs.get("staff@example.ac.kr", "https://docs.google.com/document/d/docA/edit")
    assert info["title"] == "2026 사업계획서"
    heads = [(s["index"], s["level"], s["heading"]) for s in info["sections"]]
    assert heads == [(1, 0, "2026 사업계획서"), (2, 1, "1. 추진 배경"), (3, 1, "2. 추진 계획")]
    sec = info["sections"][1]
    assert sec["end"] == 37 and sec["chars"] == len("지역 산업 수요가 늘고 있다.")
    assert "지역 산업 수요가 늘고 있다." in info["text"]
    assert gdocs.has_docs_scope("staff@example.ac.kr") and not gdocs.has_docs_scope("old@example.ac.kr")
    with pytest.raises(FileNotFoundError):
        gdocs.get("staff@example.ac.kr", "missing")
    with pytest.raises(ValueError):
        gdocs.doc_id("https://example.com/x?y=1")


def test_insert_goes_to_section_end_scrubbed_and_audited(docs_env):
    tmp_path, calls = docs_env
    r = gdocs.insert_into_section("staff@example.ac.kr", "docA", 2, "담당자 010-1234-5678 에게 문의",
                                  user="kim", data_dir=tmp_path, scrub=lambda t: t.replace("010-1234-5678", "[전화]"))
    assert r["ok"] and r["section"] == "1. 추진 배경" and r["at"] == 36      # 절 마지막 문단 끝(줄바꿈 앞)
    body = json.loads([c for c in calls if c[1].endswith(":batchUpdate")][-1][2])
    ins = body["requests"][0]["insertText"]
    assert ins["location"]["index"] == 36 and ins["text"] == "\n담당자 [전화] 에게 문의"
    rep = gdocs.replace_text("staff@example.ac.kr", "docA", "세부 과제", "세부 추진 과제", user="kim", data_dir=tmp_path)
    assert rep["count"] == 2
    audit = gdocs.recent_writes(tmp_path)
    assert [a["action"] for a in audit] == ["replace", "insert"] and audit[1]["chars"] == len("담당자 [전화] 에게 문의")
    with pytest.raises(ValueError):
        gdocs.insert_into_section("staff@example.ac.kr", "docA", 9, "x", user="kim", data_dir=tmp_path)


def test_work_page_ask_insert_replace(docs_env, tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    page = client.get("/gdocs/work", params={"doc": "https://docs.google.com/document/d/docA/edit", "account": "staff@example.ac.kr"})
    assert page.status_code == 200 and "1. 추진 배경" in page.text and "docs.google.com/document/d/docA/edit" in page.text
    listing = client.get("/gdocs/work")
    assert listing.status_code == 200
    assert 'data-drive-account' in listing.text and 'data-drive-files' in listing.text
    assert 'Google Docs 주소 또는 문서 ID' not in listing.text
    assert '연결된 Drive' in listing.text
    ask = client.post("/gdocs/ask", data={"doc": "docA", "account": "staff@example.ac.kr", "question": "추진 배경을 보강해 줘"})
    assert ask.status_code == 200 and "답변" in ask.text
    ins = client.post("/gdocs/insert", data={"doc": "docA", "account": "staff@example.ac.kr", "section": "3", "text": "새 과제를 더한다"}, follow_redirects=False)
    assert ins.status_code == 303 and "ok=" in ins.headers["location"]
    rep = client.post("/gdocs/replace", data={"doc": "docA", "account": "staff@example.ac.kr", "old": "세부", "new": "상세"}, follow_redirects=False)
    assert "ok=" in rep.headers["location"]
    bad = client.post("/gdocs/insert", data={"doc": "docA", "account": "staff@example.ac.kr", "section": "3", "text": ""}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    assert len(gdocs.recent_writes(tmp_path)) == 2


def test_chat_answer_reads_linked_google_doc_and_reports_read_failure(docs_env, tmp_path, monkeypatch):
    """대화에 연결된 구글 독스는 편집 에이전트가 맡고(모델이 없으면 문서를 바꾸지 않았다고 말함), 못 읽으면 읽은 척하지 않는다."""
    import json as _json

    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", _json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    from zzaimy.generate import client as _gc

    def _boom(*a, **k):
        raise RuntimeError("모델 서버 없음")
    monkeypatch.setattr(_gc, "VllmClient", _boom)
    r = client.post("/chat/send", data={"question": "추진 배경 보강", "session_id": str(sid)}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "문서는 바꾸지 않았습니다" in page
    # 읽기 실패 — 문서 주소가 없는 것으로 바꾸면 실패 안내
    db.set_setting(f"chat_google_doc:{sid}", _json.dumps({"doc": "missing", "account": "staff@example.ac.kr"}))
    r = client.post("/chat/send", data={"question": "다시", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "연결된 구글 문서를 읽지 못했습니다" in page
    assert client.get("/api/chat-documents/accounts").status_code == 200


class _FakeChoice:
    def __init__(self, content): self.message = type("M", (), {"content": content})()


class _FakePlanner:
    """가짜 27B — 편집 계획을 정해진 JSON 으로 낸다. 프롬프트에 문서 본문과 지시가 들어갔는지 기록한다."""

    def __init__(self, content):
        self.content, self.prompts, self.model, self._extra = content, [], "fake", {}
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.prompts.append(kw["messages"][0]["content"])
                assert kw["response_format"]["type"] == "json_schema"
                return type("R", (), {"choices": [_FakeChoice(outer.content)]})()

        self.client = type("C", (), {"chat": type("Ch", (), {"completions": _Completions()})()})()


def test_linked_doc_command_is_applied_to_the_document(docs_env, tmp_path, monkeypatch):
    """채팅 명령 → 편집 계획 → 문서에 바로 적용 → 채팅에 결과(사용자 요구: 담당자가 옮겨 넣지 않는다)."""
    import json as _json
    from zzaimy.app.main import create_app as _create

    fake = _FakePlanner(_json.dumps({"reply": "추진 배경을 보강했습니다.", "ops": [
        {"op": "insert", "section": 2, "old": "", "text": "산학협력 수요조사에서 응답 기관의 다수가 공동 교육과정을 원했다. 문의 010-9999-8888 (담당 주민번호 900101-1234568)"},
        {"op": "replace", "section": 0, "old": "세부 과제", "text": "세부 추진 과제"}]}, ensure_ascii=False))
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", _json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    calls_before = len([c for c in docs_env[1] if c[1].endswith(":batchUpdate")])
    with monkeypatch.context() as m:
        from zzaimy.generate import client as _gc
        m.setattr(_gc, "VllmClient", lambda *a, **k: fake)
        r = client.post("/chat/send", data={"question": "추진 배경을 보강해 줘", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "추진 배경을 보강했습니다" in page and "적용됨" in page and "아래에" in page and "곳" in page
    batch = [c for c in docs_env[1] if c[1].endswith(":batchUpdate")]
    assert len(batch) - calls_before == 2
    # 내부 기본 정책은 원문 활용이다. 학습 반출 보호는 별도 경로에서 검증한다.
    assert "010-9999-8888" in batch[-2][2] and "900101-1234568" in batch[-2][2]
    assert "담당자 지시" in fake.prompts[-1] and "지역 산업 수요가 늘고 있다" in fake.prompts[-1]
    # 확인 후 적용 모드: 계획만 보여 주고 쓰지 않는다 → 적용을 누르면 쓴다
    client.post(f"/api/chat-documents/{sid}/confirm-mode", data={"on": "1"})
    n = len([c for c in docs_env[1] if c[1].endswith(":batchUpdate")])
    with monkeypatch.context() as m:
        from zzaimy.generate import client as _gc
        m.setattr(_gc, "VllmClient", lambda *a, **k: fake)
        r = client.post("/chat/send", data={"question": "다시 보강", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "아직 문서에 쓰지 않았습니다" in page
    assert len([c for c in docs_env[1] if c[1].endswith(":batchUpdate")]) == n
    ap = client.post(f"/api/chat-documents/{sid}/apply-pending")
    assert ap.status_code == 200 and len(ap.json()["applied"]) == 2


def test_insert_drops_lines_already_in_document():
    from zzaimy.app.gdocs_agent import _drop_existing

    doc = "1. 추진 배경\n지역 산업 수요가 늘고 있다.\n2. 추진 계획\n세부 과제를 둔다."
    assert _drop_existing("세부 과제를 둔다.\n1) 산학협력 교육과정을 개발한다.", doc) == "1) 산학협력 교육과정을 개발한다."
    assert _drop_existing("세부 과제를 둔다.", doc) == ""



def _drive_files_transport(calls: list, folders: dict):
    """폴더 찾기·만들기, 문서 만들기, 폴더로 옮기기까지 흉내 내는 가짜 드라이브·독스 API."""
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, dict(req.url.params)))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        if req.method == "GET" and req.url.path == "/drive/v3/files":
            q = req.url.params.get("q", "")
            name = q.split("'")[1]
            hit = [{"id": fid, "name": n} for fid, (n, _p) in folders.items() if n == name]
            return httpx.Response(200, json={"files": hit})
        if req.method == "POST" and req.url.path == "/drive/v3/files":
            body = json.loads(req.content); fid = f"f{len(folders) + 1}"
            folders[fid] = (body["name"], body["parents"][0])
            return httpx.Response(200, json={"id": fid})
        if req.method == "POST" and req.url.path == "/v1/documents":
            return httpx.Response(200, json={"documentId": "newdoc"})
        if req.url.path.endswith(":batchUpdate"):
            return httpx.Response(200, json={"documentId": "newdoc", "replies": []})
        if req.method == "PATCH" and req.url.path == "/drive/v3/files/newdoc":
            calls.append(("moved", req.url.params.get("addParents"), ""))
            return httpx.Response(200, json={"id": "newdoc"})
        if req.url.path == "/v1/documents/newdoc":
            return httpx.Response(200, json={"documentId": "newdoc", "title": "새 초안", "body": {"content": [
                {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
                {"startIndex": 1, "endIndex": 6, "paragraph": {"paragraphStyle": {"namedStyleType": "TITLE"},
                                                                "elements": [{"textRun": {"content": "새 초안\n"}}]}}]}})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_auto_document_creates_folder_chain_and_binds_session(docs_env, tmp_path, monkeypatch):
    from zzaimy.ingest import gdrive_files

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls, folders = [], {}
    monkeypatch.setattr(gdrive, "_http", lambda: _drive_files_transport(calls, folders))
    db = Database(tmp_path / "t.db")
    sid = db.create_chat_session("지역혁신 계획서", owner="zzaimy")
    with pytest.raises(ValueError, match="내 구글 계정"):                       # 연결 전에는 남의 계정을 빌리지 않는다
        gdrive_files.auto_document(db, sid, "zzaimy", "지역혁신 계획서", project_name="RISE 2026")
    gdrive_files.bind_account(db, "zzaimy", "staff@example.ac.kr")
    made = gdrive_files.auto_document(db, sid, "zzaimy", "지역혁신 계획서", project_name="RISE 2026")
    assert made["doc"] == "newdoc" and made["url"].endswith("/newdoc/edit")
    names = [n for n, _p in folders.values()]
    assert names[0] == "ZZAIMY" and names[2] == "RISE 2026" and len(names) == 3      # ZZAIMY/<연도>/<프로젝트>
    assert gdrive_files.project_parts(None)[2] == "기타"                                # 프로젝트 없으면 기타 하나에
    assert ("moved", made["folder"], "") in calls
    assert json.loads(db.get_setting(f"chat_google_doc:{sid}"))["doc"] == "newdoc"
    # 두 번째는 같은 폴더를 다시 만들지 않는다
    gdrive_files.auto_document(db, sid, "zzaimy", "다른 문서", project_name="RISE 2026")
    assert len(folders) == 3
    # 파일 만들기 범위가 없으면 다시 허용을 안내한다
    t["staff@example.ac.kr"]["scopes"] = [gdrive.SCOPES[0], gdrive.SCOPES[1]]
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    with pytest.raises(PermissionError):
        gdrive_files.auto_document(db, sid, "zzaimy", "x")


def test_drafting_request_without_document_creates_one_and_writes(docs_env, tmp_path, monkeypatch):
    """새 채팅에서 '초안 써 줘' 만 하면 문서 주소 없이 폴더·문서가 생기고 에이전트가 바로 쓴다."""
    from zzaimy.app.main import create_app as _create

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls, folders = [], {}
    monkeypatch.setattr(gdrive, "_http", lambda: _drive_files_transport(calls, folders))
    fake = _FakePlanner(json.dumps({"reply": "초안을 넣었습니다.", "ops": [
        {"op": "insert", "section": 1, "old": "", "text": "1. 추진 배경\n지역 산업 수요가 늘고 있다."}]}, ensure_ascii=False))
    from zzaimy.generate import client as _gc
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    from zzaimy.ingest import gdocs_templates
    rendered = []
    monkeypatch.setattr(gdocs_templates, "render", lambda email, doc, spec, http=None, titled=False, fill=None: rendered.append((doc, spec["id"])))
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    r = client.post("/chat/send", data={"question": "2027년 지역혁신 사업계획서 초안을 써 줘. 절은 셋으로."}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "드라이브에 문서 「2027년 지역혁신 사업계획서」" in page and "만들어 이 대화에 연결했습니다" in page and "적용됨" in page
    assert "「사업계획서 공통 양식」 을 깔아 두었습니다" in page and rendered == [("newdoc", "plan")]   # 계획서 요청이면 공통 양식 위에서
    sid = int(r.headers["location"].rstrip("/").split("/")[-1])
    assert json.loads(app.state.db.get_setting(f"chat_google_doc:{sid}"))["doc"] == "newdoc"
    # 질문(초안 요청이 아님)은 문서를 만들지 않고 보통 답변
    r = client.post("/chat/send", data={"question": "휴학 처리 기준이 뭐야?"}, follow_redirects=False)
    assert "합성 답변" in client.get(r.headers["location"]).text
    # 명시적 만들기 경로
    made = client.post("/api/chat-documents/create", data={"title": "새 문서"})
    assert made.status_code == 200 and made.json()["doc"] == "newdoc"


def test_formatting_ops_style_bold_and_table(docs_env, tmp_path, monkeypatch):
    """서식: 절 제목 단계, 글귀 굵게, 절 끝에 표(셀은 뒤에서부터 채움)."""
    calls: list = []
    state = {"table": False}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.content.decode() if req.content else ""))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        if req.url.path == "/v1/documents/docA" and req.method == "GET":
            if not state["table"]:
                return httpx.Response(200, json=DOC)
            doc = json.loads(json.dumps(DOC))
            doc["body"]["content"].append({"startIndex": 36, "endIndex": 60, "table": {"tableRows": [
                {"tableCells": [{"content": [{"startIndex": 38}]}, {"content": [{"startIndex": 41}]}]},
                {"tableCells": [{"content": [{"startIndex": 45}]}, {"content": [{"startIndex": 48}]}]}]}})
            return httpx.Response(200, json=doc)
        if req.url.path == "/v1/documents/docA:batchUpdate":
            body = json.loads(req.content)
            if any("insertTable" in r for r in body["requests"]):
                state["table"] = True
            return httpx.Response(200, json={"documentId": "docA", "replies": []})
        return httpx.Response(404)
    monkeypatch.setattr(gdrive, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    st = gdocs.set_section_style("staff@example.ac.kr", "docA", 2, "heading_2", user="k", data_dir=tmp_path)
    assert st["style"] == "HEADING_2"
    req = json.loads([c for c in calls if c[1].endswith(":batchUpdate")][-1][2])["requests"][0]["updateParagraphStyle"]
    assert req["range"] == {"startIndex": 11, "endIndex": 20} and req["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    b = gdocs.emphasize("staff@example.ac.kr", "docA", "지역 산업", user="k", data_dir=tmp_path)
    assert b["count"] == 1
    req = json.loads([c for c in calls if c[1].endswith(":batchUpdate")][-1][2])["requests"][0]["updateTextStyle"]
    assert req["range"] == {"startIndex": 20, "endIndex": 25} and req["textStyle"]["bold"] is True
    t = gdocs.insert_table("staff@example.ac.kr", "docA", 2, [["항목", "값"], ["예산", "100"]], user="k", data_dir=tmp_path)
    assert t["rows"] == 2 and t["cols"] == 2
    batches = [json.loads(c[2])["requests"] for c in calls if c[1].endswith(":batchUpdate")]
    fills = [q for q in batches[-2] if "insertText" in q]
    assert [f["insertText"]["location"]["index"] for f in fills] == [48, 45, 41, 38]      # 뒤에서부터
    style = batches[-1]                                                                 # 머리 행 음영(굵게는 endIndex 가 있을 때만)
    assert style[0]["updateTableCellStyle"]["tableRange"]["columnSpan"] == 2 and style[0]["updateTableCellStyle"]["tableRange"]["tableCellLocation"]["tableStartLocation"]["index"] == 36
    assert [a["action"] for a in gdocs.recent_writes(tmp_path, 3)] == ["table", "bold", "style"]
    assert gdocs.embed_url("docA", toolbar=True).endswith("/docA/edit") and "rm=minimal" in gdocs.embed_url("docA")


def test_rename_command_renames_drive_file_and_chat_title(docs_env, tmp_path, monkeypatch):
    """"문서 이름을 사업명으로 바꿔 줘" → 드라이브 파일 이름(독스 제목)과 대화 제목이 바뀐다."""
    from zzaimy.app.main import create_app as _create

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.content.decode() if req.content else ""))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        if req.url.path == "/v1/documents/docA" and req.method == "GET":
            return httpx.Response(200, json=DOC)
        if req.method == "PATCH" and req.url.path == "/drive/v3/files/docA":
            return httpx.Response(200, json={"id": "docA", "name": json.loads(req.content)["name"]})
        return httpx.Response(404)
    monkeypatch.setattr(gdrive, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    fake = _FakePlanner(json.dumps({"reply": "문서 이름을 바꿨습니다.", "ops": [
        {"op": "rename", "section": 0, "old": "", "text": "2027 산학협력 선도대학 사업계획서"}]}, ensure_ascii=False))
    from zzaimy.generate import client as _gc
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("초안 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    r = client.post("/chat/send", data={"question": "문서 이름을 사업명으로 바꿔 줘", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "문서 이름 → 「2027 산학협력 선도대학 사업계획서」" in page
    assert any(m == "PATCH" and p == "/drive/v3/files/docA" for m, p, _ in calls)
    assert db.get_chat_session(sid)["title"] == "2027 산학협력 선도대학 사업계획서"
    assert gdocs.recent_writes(tmp_path, 1)[0]["action"] == "rename"


def test_account_binding_department_root_and_project_move(docs_env, tmp_path, monkeypatch):
    """계정이 여럿일 때: 허용을 누른 사람에게 묶이고, 부서 공용 자리가 있으면 그 아래에, 프로젝트에 넣으면 문서가 따라 옮겨진다."""
    from zzaimy.app.main import create_app as _create
    from zzaimy.ingest import gdrive_files

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    t["other@example.ac.kr"] = dict(t["staff@example.ac.kr"])
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls, folders = [], {}
    monkeypatch.setattr(gdrive, "_http", lambda: _drive_files_transport(calls, folders))
    db = Database(tmp_path / "t.db")
    # 허용 계정이 둘이면 묶이지 않은 사용자는 계정이 없다 → 본인 계정을 묶으면 그것
    assert gdrive_files.account_for(db, "kim", "학생처") == ""
    gdrive_files.bind_account(db, "kim", "staff@example.ac.kr")
    assert gdrive_files.account_for(db, "kim", "학생처") == "staff@example.ac.kr"
    db.set_setting("google_account_dept:학생처", "other@example.ac.kr")
    assert gdrive_files.account_for(db, "park", "학생처") == "other@example.ac.kr"      # 부서 공용 계정
    db.set_setting("google_root:학생처", "shared-root")
    sid = db.create_chat_session("초안", owner="kim")
    made = gdrive_files.auto_document(db, sid, "kim", "초안", dept="학생처")
    assert folders[[k for k, (n, _p) in folders.items() if n == "ZZAIMY"][0]][1] == "shared-root"   # 부서 공용 자리 아래
    assert [n for n, _p in folders.values()][-1] == "기타"                                            # 프로젝트 없음
    # 프로젝트에 넣으면 문서가 프로젝트 폴더로 옮겨진다
    pid = db.create_project("grant", "RISE 2027", owner="kim")
    moved = gdrive_files.move_to_project(db, sid, "RISE 2027", dept="학생처")
    assert moved["project"] == "RISE 2027" and any(m == "moved" and a == moved["folder"] for m, a, _ in calls)
    assert [n for n, _p in folders.values()][-1] == "RISE 2027"


def test_project_route_moves_document_and_agent_move_op(docs_env, tmp_path, monkeypatch):
    from zzaimy.app.main import create_app as _create
    from zzaimy.ingest import gdrive_files

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls, folders = [], {}
    monkeypatch.setattr(gdrive, "_http", lambda: _drive_files_transport(calls, folders))
    fake = _FakePlanner(json.dumps({"reply": "옮겼습니다.", "ops": [{"op": "move", "section": 0, "old": "", "text": "HUSS 2027"}]}, ensure_ascii=False))
    from zzaimy.generate import client as _gc
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("초안", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "newdoc", "account": "staff@example.ac.kr"}))
    pid = db.create_project("grant", "RISE 2027", owner="zzaimy")
    r = client.post(f"/chat/{sid}/project", data={"project_id": str(pid)})
    assert r.status_code == 200 and r.json()["moved"] is True
    assert db.get_chat_session(sid)["project_id"] == pid and "RISE 2027" in [n for n, _p in folders.values()]
    pid2 = db.create_project("grant", "HUSS 2027", owner="zzaimy")
    r = client.post("/chat/send", data={"question": "이 문서를 HUSS 2027 프로젝트로 옮겨 줘", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "「HUSS 2027」 폴더로 옮기고" in page and db.get_chat_session(sid)["project_id"] == pid2


def test_rename_and_move_ops_are_dropped_unless_the_user_asked(monkeypatch):
    """모델이 근거 조각을 보고 제 이름을 붙이는 일(실측 2026-09-24: 질문에 '자기소개서'로 개명) — 담당자의 말에 그 뜻이 없으면 버린다."""
    from zzaimy.app import gdocs_agent

    fake = _FakePlanner(json.dumps({"reply": "정리했습니다.", "ops": [
        {"op": "rename", "section": 0, "old": "", "text": "자기소개서"},
        {"op": "move", "section": 0, "old": "", "text": "다른 프로젝트"},
        {"op": "insert", "section": 1, "old": "", "text": "접수 문서 목록"}]}, ensure_ascii=False))
    info = {"title": "t", "sections": [{"index": 1, "heading": "1", "chars": 0}], "text": ""}
    kept = gdocs_agent.plan(fake, "이 프로젝트에 접수된 문서가 무엇인지 정리해 줘", info)["ops"]
    assert [o["op"] for o in kept] == ["insert"]
    asked = gdocs_agent.plan(fake, "문서 이름을 사업명으로 바꿔 주고 RISE 프로젝트 폴더로 옮겨 줘", info)["ops"]
    assert [o["op"] for o in asked] == ["rename", "move", "insert"]


def test_plain_questions_do_not_look_like_drafting():
    from zzaimy.app.main import create_app  # noqa: F401 — 판정 함수는 앱 안에 있어 아래처럼 꺼낸다
    import zzaimy.app.main as m

    src = m.create_app.__code__
    names = src.co_consts
    # 판정 함수는 create_app 의 지역 함수라 앱을 만들어 꺼낸다
    from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    app = create_app(db_path=d / "t.db", inbox_dir=d / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    f = app.state.looks_like_drafting
    assert not f("이 프로젝트에 접수된 문서가 무엇인지 정리해 줘")
    assert not f("계획서는 어디에 있어?")
    assert not f("이 공고에서 배점 기준을 정리해 줘")
    assert f("2027년 사업계획서 초안을 써 줘")
    assert f("회의 결과를 문서로 정리해 줘")
    assert f("안내문 작성해 줘")
    assert f("지원 신청서를 만들어 줘")


def _cell(text: str) -> dict:
    return {"content": [{"paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}}]}


def test_outline_reads_tabs_tables_and_numbered_headings_of_converted_forms():
    """한글 양식 변환본: body 가 비고 tabs 에 내용, 제목 스타일 없이 번호 문단, 본문은 표 — 그래도 절과 글이 보여야 한다."""
    content = [
        {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
        _para(1, "Ⅰ. 사업추진 목표\n"),
        _para(12, "1. 대학의 여건 및 AI·DX 특성화 방향\n"),
        _para(36, "1.1. 대학의 AI·DX 교육여건 분석\n"),
        {"startIndex": 57, "endIndex": 90, "table": {"tableRows": [{"tableCells": [_cell("【작성방법】")]},
                                                                  {"tableCells": [_cell("1) 지역 동향을 기술")]}]}},
        _para(90, "\n"),
        _para(91, "2026. 4.\n"),
        _para(100, "1.2. 대학의 AI·DX 특성화 방향\n"),
        _para(120, "\n"),
    ]
    doc = {"documentId": "d", "title": "작성서식 작업본", "body": {"content": []},
           "tabs": [{"documentTab": {"body": {"content": content}}}]}
    info = gdocs.outline(doc)
    heads = [(s["index"], s["level"], s["heading"]) for s in info["sections"]]
    assert heads == [(1, 1, "Ⅰ. 사업추진 목표"), (2, 2, "1. 대학의 여건 및 AI·DX 특성화 방향"),
                     (3, 3, "1.1. 대학의 AI·DX 교육여건 분석"), (4, 3, "1.2. 대학의 AI·DX 특성화 방향")]
    sec = info["sections"][2]
    assert "【작성방법】" in info["text"] and "1) 지역 동향을 기술" in info["text"]
    assert sec["chars"] > 0 and sec["end"] == 100          # 삽입 자리는 표가 아니라 그 뒤 문단 끝('2026. 4.' 문단)
    assert sec["table_end"] == 0                           # 표 뒤에 글 문단이 있으니 표 끝은 쓰지 않는다
    assert info["end"] == 121
    # 표(작성방법 상자)로 끝나는 절: 빈 문단만 뒤따르면 표 끝이 삽입 자리
    last = info["sections"][3]
    assert last["heading"].startswith("1.2")


def test_insert_goes_after_the_closing_table_of_a_section(monkeypatch, tmp_path):
    content = [
        {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
        _para(1, "1.1. 교육여건 분석\n"),
        _para(13, "\n"),
        {"startIndex": 14, "endIndex": 40, "table": {"tableRows": [{"tableCells": [_cell("【작성방법】 지역 동향을 기술")]}]}},
        _para(40, "1.2. 특성화 방향\n"),
        _para(52, "\n"),
    ]
    doc = {"documentId": "d", "title": "t", "body": {"content": content}}
    monkeypatch.setattr(gdocs, "get", lambda email, d, http=None: gdocs.outline(doc))
    sent = {}
    monkeypatch.setattr(gdocs, "_batch", lambda email, d, reqs, http: sent.update(reqs=reqs) or {"documentId": "d"})
    monkeypatch.setattr(gdocs, "_http", lambda: object())
    out = gdocs.insert_into_section("a@b", "d", 1, "본 대학의 여건은 다음과 같다.", user="u", data_dir=tmp_path)
    reqs = sent["reqs"]
    assert reqs[0]["insertText"]["location"]["index"] == 40 and reqs[0]["insertText"]["text"].endswith("\n")
    assert reqs[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"
    assert out["section"].startswith("1.1")


def test_drop_existing_removes_sentences_already_in_the_document():
    from zzaimy.app.gdocs_agent import _drop_existing

    doc = "1) 강점(S)\n본 대학은 AI-X추진단을 신설하여 컨트롤타워를 구축하였다. 전 학과 AI 기초교육을 필수화하였다.\n2) 약점(W)\n조정 체계가 아직 충분히 정립되지 않았다."
    text = ("본 대학은 AI-X추진단을 신설하여 컨트롤타워를 구축하였다. 조정 체계가 아직 충분히 정립되지 않았다. 새로 덧붙이는 문장이다.\n"
            "완전히 새로운 줄.")
    out = _drop_existing(text, doc)
    assert out == "새로 덧붙이는 문장이다.\n완전히 새로운 줄."
    assert _drop_existing("본 대학은 AI-X추진단을 신설하여 컨트롤타워를 구축하였다.", doc) == ""


def test_outline_carries_each_sections_text(docs_env):
    info = gdocs.get("staff@example.ac.kr", "docA")
    sec = info["sections"][1]
    assert sec["heading"] == "1. 추진 배경" and sec["text"].startswith("1. 추진 배경") and "지역 산업 수요가 늘고 있다." in sec["text"]
    assert "2. 추진 계획" not in sec["text"]
    assert sec["body_chars"] == len("지역 산업 수요가 늘고 있다.")


def test_clear_section_body_keeps_heading_and_instruction_box(monkeypatch, tmp_path):
    """다시 쓰기 전 비우기 — 제목과 【작성방법】 상자만 남기고 상자 앞뒤 문단·모델이 넣은 표를 지운다."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT"):
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": style},
                                                                 "elements": [{"textRun": {"content": text}}]}}

    def table(st, en, text):
        return {"startIndex": st, "endIndex": en, "table": {"tableRows": [{"tableCells": [{"content": [para(st + 1, en - 1, text)]}]}]}}

    body = [para(1, 20, "1.1. 교육여건 분석\n", "HEADING_2"), para(20, 22, " \n"), table(22, 80, "【작성방법】 지역 동향을 쓴다"),
            para(80, 200, "옛 문단\n"), table(200, 300, "SWOT | 내용"), para(300, 302, " \n"), para(302, 330, "1.2. 특성화 방향\n", "HEADING_2"),
            para(330, 340, "끝\n")]
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"title": "t", "body": {"content": body}})
        sent.append(json.loads(req.content)["requests"]); return httpx.Response(200, json={"documentId": "d"})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    r = gdocs.clear_section_body("a@b", "d", 1, user="u", data_dir=tmp_path, http=http)
    ranges = [(q["deleteContentRange"]["range"]["startIndex"], q["deleteContentRange"]["range"]["endIndex"]) for q in sent[0]]
    assert ranges == [(80, 301)] and r["chars"] == 221          # 상자 뒤(옛 문단+표)만 — 상자 앞 빈 문단은 독스가 못 지운다
    # 소제목 뼈대를 남기고 그 사이 본문만 — end_index 로 소제목 절들까지
    body[:] = [para(1, 20, "1.1. 교육여건 분석\n", "HEADING_2"), table(20, 80, "【작성방법】 지역 동향"), para(80, 100, "옛 글\n"),
               para(100, 120, "1. 대외여건 분석\n"), para(120, 150, "옛 소제목 본문\n"), para(150, 170, "1.2. 특성화 방향\n"), para(170, 180, "끝\n")]
    sent.clear()
    r = gdocs.clear_section_body("a@b", "d", 1, user="u", data_dir=tmp_path, http=http, end_index=150, keep_headings={"1. 대외여건 분석"})
    ranges = [(q["deleteContentRange"]["range"]["startIndex"], q["deleteContentRange"]["range"]["endIndex"]) for q in sent[0]]
    assert ranges == [(120, 149), (80, 100)]


def test_remove_instruction_boxes_counts_and_deletes(monkeypatch, tmp_path):
    import httpx

    def para(st, en, text):
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "elements": [{"textRun": {"content": text}}]}}

    def table(st, en, text):
        return {"startIndex": st, "endIndex": en, "table": {"tableRows": [{"tableCells": [{"content": [para(st + 1, en - 1, text)]}]}]}}

    body = [para(1, 10, "1.1. 절\n"), table(10, 50, "【작성방법】 지역 동향"), para(50, 60, "본문\n"), para(60, 70, "【증빙자료】\n"),
            table(70, 90, "구분 | 값"), para(90, 100, "끝\n")]
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"title": "t", "body": {"content": body}})
        sent.append(json.loads(req.content)["requests"]); return httpx.Response(200, json={"documentId": "d"})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert gdocs.remove_instruction_boxes("a@b", "d", user="u", data_dir=tmp_path, http=http, dry_run=True) == {"ok": True, "count": 2}
    assert not sent
    r = gdocs.remove_instruction_boxes("a@b", "d", user="u", data_dir=tmp_path, http=http)
    ranges = [(q["deleteContentRange"]["range"]["startIndex"], q["deleteContentRange"]["range"]["endIndex"]) for q in sent[0]]
    assert r["count"] == 2 and ranges == [(60, 70), (10, 50)]          # 데이터 표(구분|값)는 남는다


def test_section_bodies_downloads_inline_figures_in_place(monkeypatch, tmp_path):
    """작업본 도식(인라인 그림)은 그 자리에서 내려받아 ("image", …) 로 낸다 — 탭 문서의 inlineObjects, 크기는 pt. 옮기기용(images=False)은 받지 않는다."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT", obj=None):
        els = [{"textRun": {"content": text}}] if text else []
        if obj:
            els.append({"inlineObjectElement": {"inlineObjectId": obj}})
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": style}, "elements": els}}

    content = [para(1, 20, "1.1. 교육여건 분석\n", "HEADING_2"), para(20, 40, "도식 앞\n"), para(40, 42, "", obj="kix.fig"),
               para(42, 60, "도식 뒤\n"), para(60, 62, "", obj="kix.gone")]
    objects = {"kix.fig": {"inlineObjectProperties": {"embeddedObject": {"imageProperties": {"contentUri": "https://lh.example/fig"},
                                                                         "size": {"width": {"magnitude": 450, "unit": "PT"}, "height": {"magnitude": 225, "unit": "PT"}}}}},
               "kix.gone": {"inlineObjectProperties": {"embeddedObject": {"imageProperties": {"contentUri": "https://lh.example/gone"}}}}}
    fetched = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "lh.example":
            fetched.append(req.url.path)
            assert req.headers["authorization"] == "Bearer AT"
            return httpx.Response(200, content=b"\x89PNGfig") if req.url.path == "/fig" else httpx.Response(403)
        return httpx.Response(200, json={"title": "t", "tabs": [{"documentTab": {"body": {"content": content}, "inlineObjects": objects}}]})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    items = gdocs.section_bodies("a@b", "d", http=http)[0]["items"]
    assert items == [("text", "도식 앞"), ("image", {"data": b"\x89PNGfig", "width_pt": 450.0, "height_pt": 225.0}), ("text", "도식 뒤")]
    fetched.clear()
    items = gdocs.section_bodies("a@b", "d", http=http, images=False)[0]["items"]
    assert items == [("text", "도식 앞\n도식 뒤")] and not fetched


def test_section_bodies_and_migrate_skip_box_and_keep_order(monkeypatch, tmp_path):
    """옛 작업본의 절 본문(글·표, 작성방법 상자 제외)을 새 작업본의 같은 제목 절로 순서대로 옮긴다."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT"):
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": style}, "elements": [{"textRun": {"content": text}}]}}

    def table(st, en, rows):
        return {"startIndex": st, "endIndex": en, "table": {"tableRows": [{"tableCells": [{"content": [para(st + 1, st + 2, c)]} for c in r]} for r in rows]}}

    src = [para(1, 20, "1.1. 교육여건 분석\n", "HEADING_2"), table(20, 60, [["【작성방법】 지역 동향"]]), para(60, 90, "지역 산업 수요가 늘고 있다.\n"),
           table(90, 140, [["강점", "약점"], ["S1", "W1"]]), para(140, 160, "끝 문단\n"), para(160, 190, "1.2. 특성화 방향\n", "HEADING_2"), para(190, 200, " \n")]
    src[3]["table"]["tableStyle"] = {"tableColumnProperties": [{"width": {"magnitude": 100.0}}, {"width": {"magnitude": 200.0}}]}   # 열 너비도 온다
    dst = [para(1, 20, "1.1. 교육여건 분석\n", "HEADING_2"), table(20, 60, [["【작성방법】 지역 동향"]]), para(60, 62, " \n"),
           para(62, 90, "1.2. 특성화 방향\n", "HEADING_2"), para(90, 92, " \n")]
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            which = src if "/v1/documents/old" in req.url.path else dst
            return httpx.Response(200, json={"title": "t", "body": {"content": which}})
        reqs = json.loads(req.content)["requests"]; sent.append(reqs)
        for q in reqs:                                       # 실제 독스처럼 넣은 표가 다음 읽기에 보이게 한다
            if "insertTable" in q:
                at = q["insertTable"]["location"]["index"]
                dst.append(table(at, at + 30, [["", ""], ["", ""]]))
                dst.sort(key=lambda e: e["startIndex"])
        return httpx.Response(200, json={"documentId": "new", "replies": []})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    bodies = gdocs.section_bodies("a@b", "old", http=http)
    # 본문이 빈 절(1.2)도 차례에 있다 — 서식 채우기가 소제목의 부모를 알 수 있게(2026-09-29). 옮기기는 빈 절을 건너뛴다
    assert [(b["heading"], bool(b["items"])) for b in bodies] == [("1.1. 교육여건 분석", True), ("1.2. 특성화 방향", False)]
    assert [k for k, _ in bodies[0]["items"]] == ["text", "widths", "table", "text"]
    assert bodies[0]["items"][1][1] == [100.0, 200.0] and bodies[0]["items"][2][1] == [["강점", "약점"], ["S1", "W1"]]
    res = gdocs.migrate_bodies("a@b", "old", "new", user="u", data_dir=tmp_path, http=http)
    assert res == [{"heading": "1.1. 교육여건 분석", "done": "ok", "chars": len("지역 산업 수요가 늘고 있다.") + len("끝 문단"), "tables": 1, "under": ""}]
    kinds = [list(q.keys())[0] for batch in sent for q in batch]
    assert "insertText" in kinds and "insertTable" in kinds
    # 새 작업본에 없는 소제목(모델이 만든 것)은 직전에 맞춘 절 아래에 소제목 줄과 함께 들어간다
    src.extend([para(200, 230, "1. 거버넌스 기반 추진 체계\n", "HEADING_3"), para(230, 260, "거버넌스 본문\n")])
    sent.clear()
    res = gdocs.migrate_bodies("a@b", "old", "new", user="u", data_dir=tmp_path, http=http)
    assert res[-1]["heading"] == "1. 거버넌스 기반 추진 체계" and res[-1]["done"] == "ok" and res[-1]["under"] == "1.2. 특성화 방향"   # 옛 차례에서 바로 앞 절
    texts = [q["insertText"]["text"] for batch in sent for q in batch if "insertText" in q]
    assert any(t.strip().startswith("1. 거버넌스 기반 추진 체계\n거버넌스 본문") for t in texts)      # 소제목 줄과 본문은 한 번에
    sent.clear()
    res = gdocs.migrate_bodies("a@b", "old", "new", user="u", data_dir=tmp_path, http=http, only_headings={"1. 거버넌스 기반 추진 체계"})
    assert [r["heading"] for r in res] == ["1. 거버넌스 기반 추진 체계"] and res[0]["under"] == "1.2. 특성화 방향"


def test_table_grids_and_fill_table_write_only_value_cells(monkeypatch, tmp_path):
    """양식 표(총괄표)의 빈 칸을 fill 로 채운다 — 작성방법 상자는 표로 세지 않고, 글이 있는 칸은 바꾸며, 뒤 칸부터 써 인덱스가 안 밀린다."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT"):
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": style}, "elements": [{"textRun": {"content": text}}]}}

    def table(st, rows):
        idx = st + 1; trs = []
        for r in rows:
            tcs = []
            for c in r:
                tcs.append({"content": [para(idx, idx + len(c) + 1, c + "\n")]}); idx += len(c) + 2
            trs.append({"tableCells": tcs})
        return {"startIndex": st, "endIndex": idx + 1, "table": {"tableRows": trs}}, idx + 1

    box, e1 = table(20, [["【작성방법】 지표를 쓴다"]])
    grid, e2 = table(e1, [["지표명", "단위", "기준값"], ["AI 이수율", "%", ""], ["만족도", "점", "옛값"]])
    body = [para(1, 20, "2.1.1. 핵심 성과지표 총괄표\n", "HEADING_2"), box, grid, para(e2, e2 + 2, " \n"), para(e2 + 2, e2 + 20, "2.2. 자율 성과지표\n", "HEADING_2")]
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"title": "t", "body": {"content": body}})
        sent.append(json.loads(req.content)["requests"]); return httpx.Response(200, json={"documentId": "d"})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    info = gdocs.get("a@b", "d", http)
    sec = next(s for s in info["sections"] if s["heading"].startswith("2.1.1"))
    # 양식 표만 있고 값 칸이 비었으면 '아직 안 쓴 절'
    from zzaimy.app import drafting
    assert sec["tbl_cells"] == 9 and sec["tbl_empty"] == 1 and sec.get("para_chars", 0) == 0
    grids = gdocs.table_grids("a@b", "d", sec["index"], http=http, info=info)
    assert [g["n"] for g in grids] == [1] and grids[0]["rows"][1] == ["AI 이수율", "%", ""]
    text = gdocs.render_table_grids(grids)
    assert "표 1 (3행×3열)" in text and "r1: [c0] AI 이수율 | [c1] % | [c2] _" in text
    r = gdocs.fill_table("a@b", "d", sec["index"], 1, [{"row": 1, "col": 2, "text": "4.6"}, {"row": 2, "col": 2, "text": "95.7"}, {"row": 9, "col": 0, "text": "x"},
                                                      {"row": 2, "col": 1, "text": ""}],       # 빈 값 = 그 칸('점')을 비운다
                         user="u", data_dir=tmp_path, http=http)
    assert r["cells"] == 2 and r["skipped"] == 1 and r["cleared"] == 1
    reqs = sent[0]
    kinds = [list(q.keys())[0] for q in reqs]
    assert kinds == ["deleteContentRange", "insertText", "deleteContentRange", "insertText"]   # 뒤 칸(옛값 바꾸기)부터, 비우기, 빈 칸은 넣기만
    reqs = [q for q in reqs if not (q.get("deleteContentRange") and q["deleteContentRange"]["range"]["startIndex"] == grid["table"]["tableRows"][2]["tableCells"][1]["content"][0]["startIndex"])]
    old_start = grid["table"]["tableRows"][2]["tableCells"][2]["content"][0]["startIndex"]
    assert reqs[0]["deleteContentRange"]["range"] == {"startIndex": old_start, "endIndex": old_start + len("옛값")}
    assert reqs[1]["insertText"] == {"location": {"index": old_start}, "text": "95.7"}
    empty_start = grid["table"]["tableRows"][1]["tableCells"][2]["content"][0]["startIndex"]
    assert reqs[2]["insertText"] == {"location": {"index": empty_start}, "text": "4.6"}


def test_figure_op_renders_uploads_shares_briefly_and_inserts_image(monkeypatch, tmp_path):
    """figure: 모델의 도식 JSON → PNG → 드라이브 올림 → 잠깐 공개 → 절 끝에 그림 → 공개 닫기."""
    from zzaimy.app import gdocs_agent
    from zzaimy.ingest import gdrive_files
    calls = []
    monkeypatch.setattr(gdrive_files, "upload_file", lambda email, data, name, mime, folder, http=None, reuse=True: (calls.append(("upload", name, mime, folder, len(data) > 1000)), {"id": "IMG1", "url": "u"})[1])
    monkeypatch.setattr(gdrive_files, "share_anyone", lambda email, fid, http=None: (calls.append(("share", fid)), "PERM")[1])
    monkeypatch.setattr(gdrive_files, "unshare", lambda email, fid, perm, http=None: calls.append(("unshare", fid, perm)))
    monkeypatch.setattr(gdocs, "insert_image", lambda email, doc, sec, uri, **kw: (calls.append(("insert", sec, uri)), {"ok": True, "section": "1.1 절"})[1])
    spec = '{"title": "정책 동향", "layout": "cards", "blocks": [{"title": "국가", "items": ["AI 3대 강국"]}, {"title": "지역", "items": ["D5 육성"]}]}'
    lines = gdocs_agent.apply([{"op": "figure", "section": 3, "old": "", "text": spec, "table": 0, "cells": []}], "a@b", "d",
                              user="u", data_dir=tmp_path, figure_folder="F")
    assert lines == ["「1.1 절」 아래에 도식 「정책 동향」 (상자 2개)"]
    assert [c[0] for c in calls] == ["upload", "share", "insert", "unshare"]
    assert calls[0][1:] == ("도식 정책 동향.png", "image/png", "F", True) and calls[2][2].endswith("id=IMG1") and calls[3] == ("unshare", "IMG1", "PERM")
    # 도식 JSON 이 아니면 건너뛴다
    assert gdocs_agent.apply([{"op": "figure", "section": 3, "old": "", "text": "그냥 글", "table": 0, "cells": []}], "a@b", "d", user="u", data_dir=tmp_path) == ["도식 내용(JSON)이 아니라 건너뜀"]


def test_migrate_skips_form_native_paragraphs_and_tables(monkeypatch, tmp_path):
    """새 작업본(서식 변환본)에 이미 있는 서식 표·문단은 옮기지 않는다 — 재생성 뒤 같은 표가 두 벌이던 문제(2026-09-29)."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT"):
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": style}, "elements": [{"textRun": {"content": text}}]}}

    def table(st, en, rows):
        return {"startIndex": st, "endIndex": en, "table": {"tableRows": [{"tableCells": [{"content": [para(st + 1, st + 2, c)]} for c in r]} for r in rows]}}
    form_tbl = [["지표명", "단위", "기준값"], ["AI 이수율", "%", ""]]
    src = [para(1, 20, "2.1. 절\n", "HEADING_2"), table(20, 60, form_tbl), para(60, 90, "이 절의 목표는 AI 이수율 향상이다.\n"), table(90, 140, [["구분", "값"], ["가", "나"]]), para(140, 160, "2.2. 다음\n", "HEADING_2")]
    dst = [para(1, 20, "2.1. 절\n", "HEADING_2"), table(20, 60, form_tbl), para(60, 62, " \n"), para(62, 90, "2.2. 다음\n", "HEADING_2"), para(90, 92, " \n")]
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"title": "t", "body": {"content": src if "/old" in req.url.path else dst}})
        reqs = json.loads(req.content)["requests"]; sent.append(reqs)
        for q in reqs:                                       # 넣은 표가 다음 읽기에 보이게
            if "insertTable" in q:
                at = q["insertTable"]["location"]["index"]
                dst.append(table(at, at + 30, [["", ""], ["", ""]])); dst.sort(key=lambda e: e["startIndex"])
        return httpx.Response(200, json={"documentId": "new", "replies": []})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    res = gdocs.migrate_bodies("a@b", "old", "new", user="u", data_dir=tmp_path, http=http)
    assert res[0]["tables"] == 1 and res[0]["chars"] > 0                     # 서식 표(지표명…)는 안 옮기고 새 표(구분|값)만
    kinds = [list(q.keys())[0] for b in sent for q in b]
    assert kinds.count("insertTable") == 1



def test_fill_table_redirects_covered_cells_to_merge_origin_and_clears_hidden_text(monkeypatch, tmp_path):
    """병합에 덮인 칸(독스 API 에도 있음)을 지목하면 원점 칸에 넣고, 덮인 칸에 남은 보이지 않는 글은 지운다."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT"):
        return {"startIndex": st, "endIndex": en, "paragraph": {"paragraphStyle": {"namedStyleType": style}, "elements": [{"textRun": {"content": text}}]}}

    def cell(st, text, span=(1, 1)):
        return {"content": [para(st, st + len(text) + 1, text + "\n")], "tableCellStyle": {"rowSpan": span[0], "columnSpan": span[1]}}
    # 1행: [지표명(2열 병합)] [덮임] [기준값] ; 2행: [AI 이수율(2열 병합)] [덮임: 숨은 글 '옛'] [빈 값칸]
    tbl = {"startIndex": 20, "endIndex": 120, "table": {"tableRows": [
        {"tableCells": [cell(21, "지표명", (1, 2)), cell(28, ""), cell(30, "기준값")]},
        {"tableCells": [cell(40, "AI 이수율", (1, 2)), cell(50, "옛"), cell(54, "")]}]}}
    body = [para(1, 20, "2.1.1. 총괄표\n", "HEADING_2"), tbl, para(120, 122, " \n"), para(122, 140, "2.2. 다음\n", "HEADING_2")]
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"title": "t", "body": {"content": body}})
        sent.append(json.loads(req.content)["requests"]); return httpx.Response(200, json={"documentId": "d"})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    info = gdocs.get("a@b", "d", http)
    sec = next(s for s in info["sections"] if s["heading"].startswith("2.1.1"))
    grids = gdocs.table_grids("a@b", "d", sec["index"], http=http, info=info)
    text = gdocs.render_table_grids(grids)
    assert "r0: [c0] 지표명 | [c2] 기준값" in text and "r1: [c0] AI 이수율 | [c2] _" in text     # 덮인 c1 은 격자에 없다
    r = gdocs.fill_table("a@b", "d", sec["index"], 1, [{"row": 1, "col": 1, "text": "4.6"}, {"row": 1, "col": 2, "text": "18.3"}],
                         user="u", data_dir=tmp_path, http=http)
    assert r["cells"] == 2 and r["skipped"] == 0
    reqs = sent[0]
    # (1,1) 은 덮인 칸 → 원점 (1,0) 'AI 이수율' 을 바꾸지 않고… 아니, 원점은 값 칸이 아니므로 덮인 칸을 지목했으면 원점에 쓴다(여기선 라벨 칸을 덮어씀)
    kinds = [list(q.keys())[0] for q in reqs]
    assert "insertText" in kinds
    # 덮인 칸의 숨은 글 '옛'(50..52)은 지운다
    assert any(q.get("deleteContentRange", {}).get("range") == {"startIndex": 50, "endIndex": 51} for q in reqs)
    assert any(q.get("insertText") == {"location": {"index": 54}, "text": "18.3"} for q in reqs)


def test_pipe_text_in_insert_becomes_table_op():
    """모델이 표를 ' | ' 글로 내면 그 부분만 table 동작으로 — 앞뒤 글은 insert 로 남고 순서 유지, 구분 줄·양 끝 | 는 뗀다."""
    from zzaimy.app.gdocs_agent import split_pipe_tables

    text = ("현황은 다음과 같다.\n| 구분 | 2025 | 2026 |\n|---|---:|---|\n| 재학생 | 1,000 | 1,100 |\n| 교원 | 50 | 55 |\n"
            "표 뒤 설명 — 강점 | 약점 은 한 줄뿐이라 글이다.\n----")
    ops = split_pipe_tables([{"op": "insert", "section": 3, "text": text}, {"op": "style", "section": 3, "text": "HEADING_2"}])
    assert [(o["op"], o["section"]) for o in ops] == [("insert", 3), ("table", 3), ("insert", 3), ("style", 3)]
    assert ops[0]["text"] == "현황은 다음과 같다."
    assert ops[1]["text"] == "구분 | 2025 | 2026\n재학생 | 1,000 | 1,100\n교원 | 50 | 55"
    assert ops[2]["text"] == "표 뒤 설명 — 강점 | 약점 은 한 줄뿐이라 글이다.\n----"
    plain = [{"op": "insert", "section": 1, "text": "표 없음"}]
    assert split_pipe_tables(plain) == plain


def test_section_bodies_turns_docs_bullets_into_gongmun_markers(monkeypatch, tmp_path):
    """독스 글머리 목록은 부호가 글에 없다 — 단계별 □ ○ 와 번호 1. 가. 로 글에 붙여 낸다(번호는 윗 단계로 돌아오면 새로)."""
    import httpx

    def para(st, en, text, style="NORMAL_TEXT", bullet=None):
        p = {"paragraphStyle": {"namedStyleType": style}, "elements": [{"textRun": {"content": text}}]}
        if bullet:
            p["bullet"] = bullet
        return {"startIndex": st, "endIndex": en, "paragraph": p}

    content = [para(1, 10, "1.1. 절\n", "HEADING_2"),
               para(10, 20, "과제\n", bullet={"listId": "u", "nestingLevel": 0}),
               para(20, 30, "세부\n", bullet={"listId": "u", "nestingLevel": 1}),
               para(30, 40, "준비\n", bullet={"listId": "o", "nestingLevel": 0}),
               para(40, 50, "하위\n", bullet={"listId": "o", "nestingLevel": 1}),
               para(50, 60, "실행\n", bullet={"listId": "o", "nestingLevel": 0}),
               para(60, 70, "다시 하위\n", bullet={"listId": "o", "nestingLevel": 1}),
               para(70, 80, "평문\n")]
    lists = {"u": {"listProperties": {"nestingLevels": [{"glyphSymbol": "●"}, {"glyphSymbol": "○"}]}},
             "o": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}, {"glyphType": "ALPHA"}]}}}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "t", "body": {"content": content}, "lists": lists})
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    items = gdocs.section_bodies("a@b", "d", http=httpx.Client(transport=httpx.MockTransport(handler)))[0]["items"]
    assert items == [("text", "□ 과제\n○ 세부\n1. 준비\n가. 하위\n2. 실행\n가. 다시 하위\n평문")]


def test_document_read_retries_timeouts_and_server_errors(monkeypatch):
    """문서 읽기는 시간 초과·503 뒤에도 다시 읽어 성공한다(쓰기는 다시 하지 않는다)."""
    import httpx

    monkeypatch.setattr(gdocs, "READ_RETRIES", (0, 0, 0))
    monkeypatch.setattr(gdrive, "access_token", lambda email, http: "AT")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            raise httpx.ReadTimeout("slow", request=req)
        if len(seen) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"title": "t", "body": {"content": []}})
    info = gdocs.get("a@b", "d", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert info["title"] == "t" and len(seen) == 3


def test_section_append_requests_computes_table_cells_without_rereading():
    """절 끝에 글·표·글을 한 요청으로 — 표 칸 자리는 넣은 자리로 계산(칸(r,c) = 자리+4+r(2열+1)+2c, 독스 실측), 칸은 뒤에서부터 채우고
    머리 행은 칸마다 굵게, 다음 글은 표 뒤 문단에 붙는다."""
    sec = {"index": 2, "heading": "1.1 절", "start": 10, "end": 30}
    reqs = gdocs._section_append_requests(sec, 100, [("text", "앞 글"), ("table", [["구분", "값"], ["가", "1"]]), ("text", "뒤 글")])
    kinds = [next(iter(r)) for r in reqs]
    assert kinds[:3] == ["insertText", "updateParagraphStyle", "insertTable"]
    at = 29 + len("\n앞 글")                                       # 절 끝(29)에 넣은 글의 끝
    assert reqs[0]["insertText"] == {"location": {"index": 29}, "text": "\n앞 글"}
    assert reqs[2]["insertTable"] == {"location": {"index": at}, "rows": 2, "columns": 2}
    fills = [r["insertText"] for r in reqs if "insertText" in r][1:5]
    assert [f["location"]["index"] for f in fills] == [at + 11, at + 9, at + 6, at + 4]     # 뒤에서부터
    assert [f["text"] for f in fills] == ["1", "가", "값", "구분"]
    bolds = [r["updateTextStyle"]["range"] for r in reqs if "updateTextStyle" in r]
    assert bolds == [{"startIndex": at + 4, "endIndex": at + 6}, {"startIndex": at + 6 + 2, "endIndex": at + 8 + 1}]
    tail = reqs[-2]["insertText"]
    assert tail["text"] == "\n뒤 글" and tail["location"]["index"] == at + 1 + 2 + 2 * 5 + len("구분값가1")


def test_migrate_reads_destination_once_and_skips_repeated_tables(monkeypatch, tmp_path):
    """새 작업본은 한 번만 읽고(절마다 다시 읽지 않는다), 앞 절에서 넣은 표와 같은 표가 뒤에 또 나오면 건너뛴다."""
    calls = {"dst_reads": 0, "batches": []}
    monkeypatch.setattr(gdocs, "section_bodies", lambda email, doc, http=None, images=True: [
        {"heading": "1. 가", "items": [("table", [["항목", "값"], ["x", "1"]])]},
        {"heading": "2. 나", "items": [("table", [["항목", "값"], ["x", "1"]]), ("text", "새 글")]}])
    secs = [{"index": 1, "heading": "1. 가", "start": 1, "end": 10}, {"index": 2, "heading": "2. 나", "start": 10, "end": 20}]

    class R:
        status_code = 200

        def json(self):
            return {"body": {"content": []}}

    def fake_read(email, doc, http=None):
        if doc == "dst":
            calls["dst_reads"] += 1
        return R()
    monkeypatch.setattr(gdocs, "_read", fake_read)
    monkeypatch.setattr(gdocs, "outline", lambda document: {"sections": secs, "end": 21, "text": ""})
    monkeypatch.setattr(gdocs, "get", lambda email, doc, http=None: {"sections": secs, "end": 21, "text": ""})
    monkeypatch.setattr(gdocs, "_batch", lambda email, doc, reqs, http: calls["batches"].append(reqs) or {})
    res = gdocs.migrate_bodies("a@b", "src", "dst", user="u", data_dir=tmp_path, http=object())
    assert calls["dst_reads"] == 1 and len(calls["batches"]) == 2
    assert [(r["heading"], r["tables"]) for r in res] == [("1. 가", 1), ("2. 나", 0)]
    first, second = calls["batches"]                                # 뒤쪽 절(2. 나)부터
    assert not any("insertTable" in r for r in first) and any(r.get("insertText", {}).get("text") == "\n새 글" for r in first)
    assert any("insertTable" in r for r in second)


def test_section_append_counts_positions_in_utf16():
    """확장 영역 문자(󰊱, UTF-16 두 칸)가 든 표 뒤의 자리는 두 칸으로 센다 — 파이썬 글자 수로 세면 다음 표가 앞 표 칸 안으로 들어간다."""
    sec = {"index": 2, "heading": "절", "start": 10, "end": 30}
    wide = "\U000f02b1 지표"
    reqs = gdocs._section_append_requests(sec, 100, [("table", [[wide]]), ("table", [["다음"]])])
    tables = [r["insertTable"]["location"]["index"] for r in reqs if "insertTable" in r]
    first_start = 29 + 1
    assert tables == [29, first_start + 2 + 1 * 3 + len(wide) + 1]            # 󰊱 는 두 칸


def test_migrate_fills_form_table_cells_in_place(monkeypatch, tmp_path):
    """새 작업본 같은 절에 행·열이 같고 서식 칸 글이 모두 들어 있는 표가 있으면, 새 표를 붙이지 않고 그 표의 빈 칸에 값을 채운다."""
    secs = [{"index": 1, "heading": "2.1.1 총괄표", "start": 1, "end": 60}]
    def cell(start, text):
        return {"content": [{"startIndex": start, "endIndex": start + len(text) + 1,
                             "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}}]}
    form = {"startIndex": 5, "endIndex": 40, "table": {"tableRows": [
        {"tableCells": [cell(8, "지표"), cell(12, "목표")]}, {"tableCells": [cell(20, "이수율"), cell(28, "")]}]}}

    class R:
        status_code = 200

        def json(self):
            return {"body": {"content": [form]}}
    monkeypatch.setattr(gdocs, "_read", lambda email, doc, http=None: R())
    monkeypatch.setattr(gdocs, "outline", lambda document: {"sections": secs, "end": 61, "text": ""})
    monkeypatch.setattr(gdocs, "get", lambda email, doc, http=None: {"sections": secs, "end": 61, "text": ""})
    monkeypatch.setattr(gdocs, "section_bodies", lambda email, doc, http=None, images=True: [
        {"heading": "2.1.1 총괄표", "items": [("table", [["지표", "목표"], ["이수율", "80%"]])]}])
    batches = []
    monkeypatch.setattr(gdocs, "_batch", lambda email, doc, reqs, http: batches.append(reqs) or {})
    res = gdocs.migrate_bodies("a@b", "src", "dst", user="u", data_dir=tmp_path, http=object())
    assert res[0]["tables"] == 1 and batches == [[{"insertText": {"location": {"index": 28}, "text": "80%"}}]]


def test_batch_waits_and_retries_on_quota(monkeypatch):
    from types import SimpleNamespace
    from zzaimy.ingest import gdocs
    calls = []

    class Http:
        def post(self, url, headers=None, json=None):
            calls.append(1)
            code = 429 if len(calls) < 3 else 200
            return SimpleNamespace(status_code=code, text="quota", json=lambda: {"replies": []})
    monkeypatch.setattr(gdocs, "_headers", lambda email, http: {})
    monkeypatch.setattr(gdocs.time, "sleep", lambda s: None)
    assert gdocs._batch("a@b", "D", [{}], Http()) == {"replies": []} and len(calls) == 3


def test_doc_table_grids_label_tables_with_their_section(monkeypatch):
    """절을 지목하지 않은 명령에도 표마다 절 번호·표 번호·칸 번호를 보여 준다(모델이 fill 번호를 짐작하지 않게)."""
    from types import SimpleNamespace
    from zzaimy.ingest import gdocs

    def cell(t):
        return {"content": [{"paragraph": {"elements": [{"textRun": {"content": t + "\n"}}]}}]}
    tbl = {"startIndex": 20, "table": {"tableRows": [{"tableCells": [cell("사업명"), cell("")]},
                                                     {"tableCells": [cell("사업 기간"), cell("")]}]}}
    info = {"sections": [{"index": 1, "heading": "가", "start": 1}, {"index": 2, "heading": "사업 개요", "start": 10}], "end": 99, "text": ""}
    monkeypatch.setattr(gdocs, "_read", lambda e, d, h: SimpleNamespace(status_code=200, json=lambda: {}))
    monkeypatch.setattr(gdocs, "body_content", lambda doc: [tbl])
    grids = gdocs.doc_table_grids("a@b", "D", http=object(), info=info)
    assert [(g["section_index"], g["n"]) for g in grids] == [(2, 1)]
    text = gdocs.render_table_grids(grids)
    assert "fill 의 section=2, table=1" in text and "r1: [c0] 사업 기간 | [c1] _" in text


def test_guide_anchor_puts_text_after_guide_before_first_table():
    """공통 양식 절: 새 글은 지침(과 앞서 넣은 글) 뒤·첫 표 앞에. 지침이 없는 절은 None(기존대로 절 끝)."""
    from zzaimy.ingest import gdocs

    def para(st, en, t):
        return {"startIndex": st, "endIndex": en, "paragraph": {"elements": [{"textRun": {"content": t + "\n"}}]}}
    body = [para(1, 10, "1. 현황"), para(10, 40, gdocs.GUIDE_PREFIX + "지표로 적는다"), para(40, 60, "앞서 넣은 글"),
            {"startIndex": 60, "endIndex": 90, "table": {}}, para(90, 95, ""), para(95, 100, "2. 다음")]
    info = {"sections": [{"index": 1, "heading": "1. 현황", "start": 1}, {"index": 2, "heading": "2. 다음", "start": 95}], "end": 100}
    assert gdocs._guide_anchor(body, info, info["sections"][0]) == 59
    assert gdocs._guide_anchor(body, info, info["sections"][1]) is None


def test_plan_splits_insert_that_carries_table_cells():
    """본문 넣기에 표 칸이 실려 오면 insert 와 fill 로 나눈다 — 칸이 조용히 버려지지 않게."""
    from zzaimy.app import gdocs_agent

    fake = _FakePlanner(json.dumps({"reply": "썼습니다.", "ops": [
        {"op": "insert", "section": 4, "old": "", "text": "본문", "table": 2, "cells": [{"row": 1, "col": 1, "text": "강점"}]}]}, ensure_ascii=False))
    info = {"title": "t", "sections": [{"index": 4, "level": 2, "heading": "1. 현황", "chars": 0}], "text": "1. 현황"}
    p = gdocs_agent.plan(fake, "절을 써 줘", info)
    assert [(o["op"], o.get("table")) for o in p["ops"]] == [("insert", 0), ("fill", 2)]


def test_drafting_in_project_with_form_asks_form_or_common(docs_env, tmp_path, monkeypatch):
    """프로젝트에 사업의 작성 서식이 있으면 바로 공통 양식을 깔지 않고 고르게 한다 — 서식 우선, 짐작 금지."""
    from zzaimy.app.main import create_app as _create

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls, folders = [], {}
    monkeypatch.setattr(gdrive, "_http", lambda: _drive_files_transport(calls, folders))
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    db = app.state.db
    pid = db.create_project("grant", "지역혁신 2027", owner="zzaimy")
    db.add_document("2027 지역혁신 사업계획서 작성서식.hwpx", str(tmp_path / "a.hwpx"), doc_type="grant", project_id=pid)
    db.add_document("참여 동의서 양식.hwp", str(tmp_path / "b.hwp"), doc_type="grant", project_id=pid)     # 다른 갈래 서식은 후보가 아니다
    r = client.post("/chat/send", data={"question": "올해 계획서 초안 작성해 줘", "project_id": str(pid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "작성 서식이 있습니다" in page and "작성서식" in page and "참여 동의서" not in page
    assert db.get_setting(f"chat_google_doc:{int(r.headers['location'].rstrip('/').split('/')[-1])}", "") in ("", None)   # 아직 문서를 만들지 않았다


def test_common_template_sections_are_writable_and_guides_are_not_body():
    """독스 공통 양식(Ⅰ. → 1. → 가.): 회색 작성 지침만 있는 끝 절은 「안 쓴 절」이고 쓸 수 있다 — 「다음 절」이 첫 끝 절을 고른다."""
    from zzaimy.app import drafting

    content = [_para(1, "Ⅰ. 추진 배경 및 목표\n", "HEADING_1"),
               _para(30, "1. 추진 배경 및 필요성\n", "HEADING_2"),
               _para(60, gdocs.GUIDE_PREFIX + "공고의 사업 목적을 이어서 쓴다.\n"),
               _para(100, "2. 중장기 발전계획과의 연계\n", "HEADING_2"),
               _para(130, gdocs.GUIDE_PREFIX + "발전계획 문구는 원문 그대로.\n"),
               _para(170, "본문이 이미 있다.\n")]
    info = gdocs.outline({"body": {"content": content}})
    secs = {s["heading"]: s for s in info["sections"]}
    first, second, chapter = secs["1. 추진 배경 및 필요성"], secs["2. 중장기 발전계획과의 연계"], secs["Ⅰ. 추진 배경 및 목표"]
    assert first["body_chars"] == 0 and drafting.is_unfilled(first) and drafting.writable(first)
    assert not drafting.is_unfilled(second) and not drafting.writable(chapter)
    assert [s["heading"] for s in drafting.target_sections(info, "다음 절 작성해 줘")] == ["1. 추진 배경 및 필요성"]


def test_whole_document_draft_request_goes_to_section_agent():
    """「사업계획서 초안 작성해 줘」 — 절을 말하지 않아도 절 작성 에이전트가 빈 절 앞쪽부터 쓴다."""
    from zzaimy.app import drafting

    assert drafting.looks_like_section_draft("사업계획서 초안 작성해 줘")
    assert not drafting.looks_like_section_draft("사업계획서 제출 기한이 언제야?")
    secs = [{"index": i, "level": 2, "heading": f"{i}. 추진 내용", "leaf": True, "body_chars": 0} for i in range(1, 6)]
    got = drafting.target_sections({"sections": secs}, "사업계획서 초안 작성해 줘")
    assert [s["index"] for s in got] == [1, 2]


def test_measure_only_lines_are_not_sections_and_skipped_sections_are_passed():
    """「2cm」 같은 치수 줄은 쓸 절이 아니고, 모델이 넘긴 절은 「다음 절」에서 건너뛴다(같은 절에서 맴돌지 않게)."""
    from zzaimy.app import drafting

    secs = [{"index": 1, "level": 2, "heading": "2cm", "leaf": True, "body_chars": 0},
            {"index": 2, "level": 2, "heading": "1. 대학의 여건", "leaf": True, "body_chars": 0},
            {"index": 3, "level": 2, "heading": "2. 추진 목표", "leaf": True, "body_chars": 0}]
    info = {"sections": secs}
    assert not drafting.writable(secs[0])
    assert [s["index"] for s in drafting.target_sections(info, "다음 절 작성해 줘")] == [2]
    assert [s["index"] for s in drafting.target_sections(info, "다음 절 작성해 줘", skip={"1. 대학의 여건"})] == [3]


def test_trash_older_sends_only_previous_versions_of_same_form(monkeypatch):
    """새 완성본을 올리기 전에 같은 서식의 옛 완성본만 휴지통으로 — 다른 서식·작업본은 둔다."""
    from types import SimpleNamespace
    from zzaimy.ingest import gdrive_files

    trashed = []

    class Http:
        def get(self, url, headers=None, params=None):
            files = [{"id": "a", "name": "작성서식 완성본 2026-10-07.hwpx"}, {"id": "b", "name": "작성서식 작업본 2026-10-07"},
                     {"id": "c", "name": "다른서식 완성본 2026-10-07.hwpx"}]
            return SimpleNamespace(status_code=200, json=lambda: {"files": files})

        def patch(self, url, headers=None, params=None, json=None):
            trashed.append(url.rsplit("/", 1)[-1])
            return SimpleNamespace(status_code=200)
    monkeypatch.setattr(gdrive_files, "_headers", lambda email, http: {})
    assert gdrive_files.trash_older("a@b", "F", "작성서식 완성본 ", ".hwpx", http=Http()) == ["작성서식 완성본 2026-10-07.hwpx"]
    assert trashed == ["a"]


def test_multiline_replace_falls_back_to_line_by_line(monkeypatch, tmp_path):
    """여러 줄을 한 번에 바꾸려다 0곳이면 줄마다 — 짧고 흔한 줄(「-」)은 건드리지 않는다."""
    from zzaimy.app import gdocs_agent
    from zzaimy.ingest import gdocs as _g

    calls = []

    def fake_replace(account, doc, old, new, **kw):
        calls.append(old)
        return {"count": 0 if "\n" in old else 1}
    monkeypatch.setattr(_g, "replace_text", fake_replace)
    ops = [{"op": "replace", "old": "□ (세부)과제명: 0000\n-", "text": "□ (세부)과제명: AI 교육 혁신\n- 배경"}]
    lines = gdocs_agent.apply(ops, "a@b", "D", user="u", data_dir=tmp_path)
    assert calls == ["□ (세부)과제명: 0000\n-", "□ (세부)과제명: 0000"] and "1곳" in lines[0]


def test_empty_form_table_rows_count_as_unfilled():
    """머리행만 채운 양식 표(값 행은 빈칸) — 칸 수는 표 구조에서 세어 「안 쓴 절」로 본다(글에서는 빈 행이 빠져 다 쓴 표로 보이던 것)."""
    from zzaimy.app import drafting

    def cell(t):
        return {"content": [{"paragraph": {"elements": [{"textRun": {"content": t + "\n"}}]}}]}
    tbl = {"startIndex": 60, "endIndex": 120, "table": {"tableRows": [
        {"tableCells": [cell("계획"), cell("과제"), cell("내용")]}] + [{"tableCells": [cell(""), cell(""), cell("")]} for _ in range(3)]}}
    content = [_para(1, "Ⅰ. 배경\n", "HEADING_1"), _para(20, "2. 발전계획과의 연계\n", "HEADING_2"),
               _para(40, gdocs.GUIDE_PREFIX + "원문 그대로.\n"), tbl, _para(120, "\n")]
    sec = next(s for s in gdocs.outline({"body": {"content": content}})["sections"] if s["heading"].startswith("2."))
    assert sec["tbl_cells"] == 12 and sec["tbl_empty"] == 9 and drafting.is_unfilled(sec)


def test_generic_draft_title_gets_project_name(docs_env, tmp_path, monkeypatch):
    """「사업계획서 초안 작성해 줘」 처럼 일반 낱말뿐인 제목은 프로젝트 이름을 앞에 — 어느 사업 문서인지 드라이브에서 알 수 있게."""
    from zzaimy.app.main import create_app as _create
    from zzaimy.ingest import gdocs_templates

    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["staff@example.ac.kr"]["scopes"] = gdrive.SCOPES
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    calls, folders = [], {}
    monkeypatch.setattr(gdrive, "_http", lambda: _drive_files_transport(calls, folders))
    monkeypatch.setattr(gdocs_templates, "render", lambda email, doc, spec, http=None, titled=False, fill=None: None)
    fake = _FakePlanner(json.dumps({"reply": "썼습니다.", "ops": []}, ensure_ascii=False))
    from zzaimy.generate import client as _gc
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    pid = app.state.db.create_project("grant", "지역혁신 2027", owner="zzaimy")
    r = client.post("/chat/send", data={"question": "사업계획서 초안 작성해 줘", "project_id": str(pid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "「지역혁신 2027 사업계획서」" in page
    sid = int(r.headers["location"].rstrip("/").split("/")[-1])
    assert json.loads(app.state.db.get_setting(f"chat_google_doc:{sid}"))["template"] == "plan"


def test_replace_does_not_touch_section_headings(tmp_path, monkeypatch):
    """절 제목 글을 문장으로 바꾸는 replace 는 막는다 — 절 구조가 깨진다(제목을 바꾸라는 지시일 때만 허용)."""
    from zzaimy.app import gdocs_agent
    from zzaimy.ingest import gdocs as _g

    calls = []
    monkeypatch.setattr(_g, "replace_text", lambda *a, **k: calls.append(a[2]) or {"count": 1})
    lines = gdocs_agent.apply([{"op": "replace", "old": "사업 개요", "text": "본 사업은 …"}], "a@b", "D", user="u",
                              data_dir=tmp_path, headings={"사업개요"})
    assert calls == [] and "절 제목" in lines[0]


def test_bundle_intake_defers_review(tmp_path, monkeypatch):
    """묶음 접수는 검토 의견을 뒤로 미룬다 — 문서는 먼저 쓸 수 있고(status reviewed), 검토 의견은 대기열이 이어 쓴다."""
    import time as _t
    monkeypatch.delenv("ZZAIMY_LIGHT_PROCESS", raising=False)
    from zzaimy.app import pipeline

    seen = {}

    class P(pipeline.DocumentProcessor):
        def _review_with_retry(self, doc_id, text, doc_type, tries=3):
            _t.sleep(0.2)
            return "검토 끝"
    db = __import__("zzaimy.app.db", fromlist=["Database"]).Database(tmp_path / "t.db")
    f = tmp_path / "a.txt"
    f.write_text("2026 사업 공고\n지원 대상은 전문대학이다. 사업 기간은 2년이다." * 20, encoding="utf-8")
    did = db.add_document(filename="a.txt", stored_path=str(f), doc_type="grant")
    P().process(db, did, f, defer_review=True)
    d = db.get_document(did)
    seen["first"] = (d["status"], d["ai_review"])
    pipeline._REVIEW_QUEUE.submit(lambda: None).result(timeout=30)          # 대기열이 앞 일을 끝낼 때까지
    assert seen["first"] == ("reviewed", pipeline.REVIEW_PENDING)
    assert db.get_document(did)["ai_review"] == "검토 끝"


def test_summary_section_is_written_last():
    """「사업 요약」 같은 요약 절은 본문 절을 다 쓴 뒤 — 다음 절 차례의 맨 뒤."""
    from zzaimy.app import drafting

    secs = [{"index": 1, "level": 2, "heading": "사업 요약", "leaf": True, "body_chars": 0},
            {"index": 2, "level": 2, "heading": "1. 추진 배경", "leaf": True, "body_chars": 0}]
    assert [s["index"] for s in drafting.target_sections({"sections": secs}, "다음 절 작성해 줘")] == [2]


def test_library_hits_use_viewer_scope(monkeypatch, tmp_path):
    """절 재료의 문서함 검색은 담당자 열람 범위로 — 학생 공간·사업 문서 끈 공간은 검색하지 않는다."""
    from zzaimy.app import drafting, grant_search
    from zzaimy.app.db import Database

    calls = []

    def fake(db, q, k=6, user=None, prefer_docs=None, depts=None):
        calls.append((user, depts))
        return {"hits": [{"doc_id": 9, "chunk_id": 1, "content": "□ 추진 배경 ○ 지난 연차에는 지역 기업 수요 조사 결과를 반영해 교육과정을 개편하고 현장실습을 늘렸다. ○ 이번 연차에는 그 성과를 학과 전체로 넓히고, 협약기업과 공동 운영하는 프로그램을 두 배로 늘려 지역 취업으로 잇는다.", "path": ["LINC", "2023", "계획서"]}]}
    monkeypatch.setattr(grant_search, "search", fake)
    db = Database(tmp_path / "t.db")
    m = drafting.Materials(db, {"id": 1, "name": "사업 A", "sector": "grant"}, set(), None, None, [], scope={"user": "kim", "grant_depts": ["산학협력단"]})
    got = m.library_hits({"heading": "1. 추진 배경"}, "배경")
    assert calls == [("kim", ["산학협력단"])] and got[0]["how"].startswith("문서함 검색")
    assert drafting.Materials(db, None, set(), None, None, [], scope={"role": "student"}).library_hits({"heading": "x"}, "x") == []


def test_useful_chunk_drops_toc_and_image_only():
    from zzaimy.app.drafting import useful_chunk

    assert not useful_chunk("목 차 Ⅰ. 사업추진 목표 1. 대학의 여건 1.1. 교육여건 분석 1.2. 특성화 방향")
    assert not useful_chunk("![image](image_001.jpg) 2-2-1 산학협력 선도모델 창출 ![image](image_002.jpg)")
    assert not useful_chunk("1.외부환경 분석   2.내부역량 분석   3.의견수렴 분석   4.SWOT 분석   5.전략방향 도출")
    assert useful_chunk("□ 배경 및 목적 ○ 지역은 신산업 다섯 분야를 지목해 인재 양성과 기업 지원을 강화하고 있으며 대학은 이에 맞춰 교육과정을 개편한다. "
                        "○ 참여 학과는 현장실습과 캡스톤 디자인을 늘리고, 지역 기업과 공동 과제를 운영해 졸업생의 지역 취업을 높인다.")
    assert useful_chunk("1.외부환경 분석   2.내부역량 분석   3.의견수렴 분석   4.SWOT 분석   5.전략방향 도출   "
                        + "앞머리 목차 뒤에 본문이 길게 이어지면 그 본문은 재료로 쓴다 " * 8)


def test_number_check_flags_numbers_missing_from_evidence():
    """넣는 글의 수치가 근거에 없으면 짚는다(절대 규칙 1) — 근거에 있는 값·표기 바뀐 값은 통과."""
    from zzaimy.app import gdocs_agent

    ops = [{"op": "insert", "text": "취업률은 31.7%에서 45.7%로 높인다. 예산은 6천만 원이다."},
           {"op": "fill", "cells": [{"row": 1, "col": 1, "text": "120명"}]}]
    got = gdocs_agent.number_check(ops, ["목표 취업률 1차년 31.7%", "사업비 60,000,000원"])
    assert len(got) == 2 and any("45.7" in g for g in got) and any("120" in g for g in got)   # 31.7·6천만(=60,000,000)은 근거에 있다


def test_newest_in_family_picks_latest_source_version(tmp_path):
    """같은 계열의 판이 후보에 여럿이면 원본 수정 시각이 가장 늦은 판 — 계열이 없는 문서는 그대로."""
    from zzaimy.app import grant_search
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    a = db.add_document(filename="계획서_v1.hwp", stored_path="x1", doc_type="grant")
    b = db.add_document(filename="계획서_최종.hwp", stored_path="x2", doc_type="grant")
    c = db.add_document(filename="다른 문서.hwp", stored_path="x3", doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET family = 'f1' WHERE id IN (?, ?)", (a, b))
        conn.execute("CREATE TABLE IF NOT EXISTS archive_files (rel TEXT, doc_id INTEGER, mtime REAL, size INTEGER)")   # 운영의 원본 장부(필요한 칸만)
        conn.execute("INSERT INTO archive_files (rel, doc_id, mtime, size) VALUES ('a', ?, 100, 1)", (a,))
        conn.execute("INSERT INTO archive_files (rel, doc_id, mtime, size) VALUES ('b', ?, 200, 1)", (b,))
    got = grant_search._newest_in_family(db, {a, b, c})
    assert got[a] == b and got[b] == b and got.get(c, c) == c          # 혼자인 계열은 자기 자신


def test_newest_in_family_groups_revision_dated_regulations(tmp_path):
    """「이름 · 개정일」 판들은 계열 열쇠가 달라도 한 규정 — 개정일이 늦은 판이 남고, 다른 규정·날짜 없는 문서는 그대로."""
    from zzaimy.app import grant_search
    from zzaimy.app.db import Database
    from zzaimy.app.doc_family import family_key

    db = Database(tmp_path / "t.db")
    names = ["영남이공대학교 학칙 · 2014년 2월 03일", "영남이공대학교 학칙 · 2026년 02월 27일",
             "영남이공대학교 학칙 · 2016년 12월 16일", "학적변동자 처리에 관한 내규 · 2012년 2월 23일", "회의록(20240315).hwp"]
    ids = [db.add_document(filename=n, stored_path=f"x{i}", doc_type="grant") for i, n in enumerate(names)]
    with db._conn() as conn:
        for i, n in zip(ids, names):
            conn.execute("UPDATE documents SET family = ? WHERE id = ?", (family_key(n), i))
        conn.execute("CREATE TABLE IF NOT EXISTS archive_files (rel TEXT, doc_id INTEGER, mtime REAL, size INTEGER)")
        conn.execute("INSERT INTO archive_files (rel, doc_id, mtime, size) VALUES ('a', ?, 999, 1)", (ids[0],))   # 늦게 복사된 옛 판
    got = grant_search._newest_in_family(db, set(ids))
    assert got[ids[0]] == ids[1] and got[ids[2]] == ids[1] and got[ids[1]] == ids[1]
    assert got.get(ids[3], ids[3]) == ids[3] and got.get(ids[4], ids[4]) == ids[4]


def test_promote_revisions_swaps_to_newest_identical_article(tmp_path):
    """옛 개정판에서 고른 조각은 최신 판 조각으로 — 같은 글이든 고쳐진 조항이든. 그 판의 연도를 물으면 그대로."""
    from zzaimy.app import grant_search
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    old = db.add_document(filename="학칙 · 2014년 2월 03일", stored_path="a", doc_type="grant")
    new = db.add_document(filename="학칙 · 2026년 02월 27일", stored_path="b", doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status = 'reviewed', access_level = 'public'")
        for did, seq, text in ((old, 1, "③ 휴학기간은 1회에 1년을 넘지 못한다."), (old, 2, "제20조(제적) 옛 글"),
                               (new, 5, "③ 휴학기간은 1회에 1년을 넘지 못한다."), (new, 6, "제20조(제적) 바뀐 글")):
            conn.execute("INSERT INTO doc_chunks (doc_id, seq, kind, content) VALUES (?, ?, 'text', ?)", (did, seq, text))
        ids = {r[1]: int(r[0]) for r in conn.execute("SELECT id, content FROM doc_chunks WHERE doc_id = ?", (old,)).fetchall()}
    hits = [{"chunk_id": ids[t], "doc_id": old, "filename": "학칙 · 2014년 2월 03일", "content": "x", "path": ["학칙 · 2014년 2월 03일"]}
            for t in ("③ 휴학기간은 1회에 1년을 넘지 못한다.", "제20조(제적) 옛 글")]
    moved = grant_search._promote_revisions(db, hits, None, None, "휴학은 얼마나?")
    assert moved == {"same": 1, "revised": 1, "stale": 0}
    assert hits[0]["doc_id"] == new and hits[0]["path"] == ["학칙 · 2026년 02월 27일"]
    assert hits[0]["content"].startswith("③ 휴학기간은")
    assert hits[1]["doc_id"] == new and hits[1]["content"].startswith("제20조(제적) 바뀐 글")   # 고쳐진 조항은 최신 글로
    old_hits = [{"chunk_id": ids["제20조(제적) 옛 글"], "doc_id": old, "filename": "학칙 · 2014년 2월 03일", "content": "x", "path": []}]
    assert grant_search._promote_revisions(db, old_hits, None, None, "2014년 학칙의 제적") == {"same": 0, "revised": 0, "stale": 0}
    assert old_hits[0]["doc_id"] == old                                   # 옛 판을 물으면 그대로
    with db._conn() as conn:
        conn.execute("INSERT INTO doc_chunks (doc_id, seq, kind, content) VALUES (?, 9, 'text', '부칙 경과조치는 따로 정한다')", (old,))
        gone = int(conn.execute("SELECT id FROM doc_chunks WHERE doc_id = ? AND seq = 9", (old,)).fetchone()[0])
    stale = [{"chunk_id": gone, "doc_id": old, "filename": "학칙 · 2014년 2월 03일", "content": "부칙 경과조치는 따로 정한다", "path": []}]
    assert grant_search._promote_revisions(db, stale, None, None, "경과조치")["stale"] == 1
    assert stale[0]["doc_id"] == old and stale[0]["content"].startswith("[옛 개정판")   # 최신 판에 없는 글은 옛 판이라고 적는다


def test_report_project_also_fetches_matching_plan(monkeypatch, tmp_path):
    """실적보고서 프로젝트면 같은 사업·연차 계획서도 따로 찾아 재료 앞에 둔다(계획 대비 실적을 쓰려면)."""
    from zzaimy.app import drafting, grant_search
    from zzaimy.app.db import Database

    qs = []
    body = "□ 추진 내용 ○ 지역 기업과 공동 교육과정을 운영하고 현장실습을 늘려 졸업생의 지역 취업을 높이는 데 집중했다. ○ 참여 학과를 넓히고 협약기업과 공동 운영하는 프로그램을 늘렸다. " * 2

    def fake(db, q, k=6, user=None, prefer_docs=None, depts=None):
        qs.append(q)
        return {"hits": [{"doc_id": len(qs), "chunk_id": len(qs), "content": body + str(len(qs)), "path": ["사업", "2024", "x"]}]}
    monkeypatch.setattr(grant_search, "search", fake)
    m = drafting.Materials(Database(tmp_path / "t.db"), {"id": -1, "name": "사업 A 2024년 실적보고서", "sector": "grant"}, set(), None, None, [],
                           scope={"user": "kim"})
    got = m.library_hits({"heading": "가. 추진 실적"}, "실적")
    assert any("계획서" in q and "실적보고서" not in q for q in qs)
    assert got[0]["how"].startswith("문서함 검색(같은 연차 계획서)")


def test_ask_hints_hide_internal_cell_numbers():
    from zzaimy.app.gdocs_agent import _plain

    assert _plain("가. 과제 개요 표의 r0 c1 칸에 입력할 과제 명칭") == "가. 과제 개요 입력할 과제 명칭"
    assert "r3" not in _plain("핵심 성과지표 기준값(r3, c2)")


def test_doc_level_guides_reach_section_materials():
    """문서 앞머리의 작성 지침(개조식 규칙)이 절 재료에 들어간다."""
    from zzaimy.app import drafting

    content = [_para(1, "사업계획서\n", "TITLE"), _para(20, gdocs.GUIDE_PREFIX + "본문은 개조식으로 쓴다(□ → ○ → -).\n"),
               _para(70, "Ⅰ. 배경\n", "HEADING_1"), _para(80, "1. 추진 배경\n", "HEADING_2")]
    info = gdocs.outline({"body": {"content": content}})
    rules = drafting.doc_guides(info)
    assert "개조식" in rules
    assert "[문서 전체 작성 규칙" in drafting.render_materials({"doc_rules": rules, "instructions": "x"})


def test_source_check_flags_invented_document_names():
    from zzaimy.app import gdocs_agent

    ops = [{"op": "insert", "text": "지역 신산업을 키운다(출처: 대구광역시 신산업 육성 계획). 성과지표를 정했다(출처: 성과지표 기준값 설정 근거 자료, 3쪽)."}]
    got = gdocs_agent.source_check(ops, ["《2025년 성과지표 기준값 설정 근거 자료》 (문서함 검색)\n본문"])
    assert got == ["대구광역시 신산업 육성 계획"]


def test_one_project_entry_with_optional_files(docs_env, tmp_path):
    """프로젝트 만들기 한 곳 — 자료 없이 이름만이면 그냥 만들고, 이름도 자료도 없으면 400."""
    from zzaimy.app.main import create_app as _create

    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    c = TestClient(app)
    r = c.post("/projects/bundle", data={"sector": "grant", "name": "지역혁신 2027"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/project/")
    assert app.state.db.get_project(int(r.headers["location"].rsplit("/", 1)[1]))["name"] == "지역혁신 2027"
    assert c.post("/projects/bundle", data={"sector": "grant"}, follow_redirects=False).status_code == 400


def test_single_level_template_sections_are_writable():
    """단위 프로그램 양식처럼 절이 제목 1단계뿐이어도 끝 절이면 쓸 절 — 소제목을 거느린 장 제목은 아니다."""
    from zzaimy.app import drafting

    assert drafting.writable({"heading": "2. 추진 배경 및 필요성", "level": 1, "leaf": True})
    assert not drafting.writable({"heading": "Ⅰ. 사업 추진 배경 및 목표", "level": 1, "leaf": False})
    assert not drafting.writable({"heading": "사업계획서", "level": 0, "leaf": True})


def test_replace_falls_back_to_box_cell(monkeypatch, tmp_path):
    """찾아 바꾸기가 0곳이고 바꿀 글이 서식 상자(표 칸) 안에 띄어쓰기만 다르게 있으면 그 칸을 고쳐 채운다 — 줄바꿈은 살린다."""
    from zzaimy.app import gdocs_agent
    from zzaimy.ingest import gdocs as _g

    filled = []
    monkeypatch.setattr(_g, "replace_text", lambda *a, **k: {"count": 0})
    monkeypatch.setattr(_g, "doc_table_grids", lambda account, doc, http=None, info=None, sep=" ": [
        {"n": 1, "section_index": 4, "covered": set(), "rows": [["□ (세부)과제명: 0000\n1. 추진배경\n- 현황 분석"]]}])
    monkeypatch.setattr(_g, "fill_table", lambda account, doc, sec, n, cells, **k: filled.append((sec, n, cells)) or {})
    lines = gdocs_agent.apply([{"op": "replace", "old": "□ (세부)과제명:  0000", "text": "□ (세부)과제명: AI 교육 혁신"}],
                              "a@b", "D", user="u", data_dir=tmp_path)
    assert filled == [(4, 1, [{"row": 0, "col": 0, "text": "□ (세부)과제명: AI 교육 혁신\n1. 추진배경\n- 현황 분석"}])]
    assert "1곳" in lines[0]


def test_quoted_heading_targets_that_section_for_redo():
    """「절 제목」 절로 짚으면 그 절 — 번호가 한 마디인 공통 양식에서도 「검토 의견 반영해 다시 쓰기」가 맞는 절로 간다."""
    from zzaimy.app import drafting
    from zzaimy.app.gdocs_agent import _plain

    secs = [{"index": 1, "level": 2, "heading": "1. 추진 배경", "leaf": True, "body_chars": 0},
            {"index": 2, "level": 2, "heading": "3. 대학 여건 및 현황 분석", "leaf": True, "body_chars": 500, "para_chars": 500}]
    q = "「3. 대학 여건 및 현황 분석」 절을 방금 검토 의견을 반영해 다시 써 줘"
    assert drafting.looks_like_section_draft(q)
    assert [s["index"] for s in drafting.target_sections({"sections": secs}, q)] == [2]
    assert "r1" not in _plain("표 1의 r1~r3 행 [c0] 열에 들어갈 발전계획 전략")


def test_bullet_layout_indents_by_marker():
    """넣은 개조식 글 — □ 는 들여쓰지 않고 굵게, ○ 한 단계, - 두 단계. 기호 없는 줄은 그대로. 인덱스는 줄마다 이어진다."""
    from zzaimy.ingest import gdocs
    text = "□ 정책 환경\n○ 지역 수요 증가\n- 세부 설명\n일반 문장"
    reqs = gdocs.bullet_layout(text, 10, font="Noto Sans KR")
    paras = [r["updateParagraphStyle"] for r in reqs if "updateParagraphStyle" in r]
    assert [p["paragraphStyle"]["indentStart"]["magnitude"] for p in paras] == [13, 23, 31]
    assert paras[0]["range"] == {"startIndex": 10, "endIndex": 10 + len("□ 정책 환경") + 1}
    assert paras[1]["range"]["startIndex"] == paras[0]["range"]["endIndex"]
    bolds = [r for r in reqs if "updateTextStyle" in r and r["updateTextStyle"]["fields"] == "bold"]
    assert len(bolds) == 1                                                  # □ 줄만 굵게
    assert reqs[-1]["updateTextStyle"]["textStyle"]["weightedFontFamily"]["fontFamily"] == "Noto Sans KR"
    assert gdocs.bullet_layout("기호 없는 글", 1) == []


def test_report_section_looks_up_plan_counterpart_section(monkeypatch, tmp_path):
    """보고서 절은 계획서의 짝 절 이름을 「」로 묶어 찾고, 그래프 절이 걸린 첫 후보를 쓴다(보고서 절 제목으로는 계획서에서 못 찾았다)."""
    from zzaimy.app import drafting, grant_search
    from zzaimy.app.db import Database

    qs = []
    body = "□ 추진 과제 ○ 기업 맞춤 교육과정 운영과 현장실습 확대, 협약기업과 공동 운영하는 프로그램을 늘려 지역 취업을 높인다. " * 2

    def fake(db, q, k=6, user=None, prefer_docs=None, depts=None):
        qs.append(q)
        hit = "「차년도 사업계획」" in q
        return {"steps": ["[그래프 절] 걸림"] if hit else [], "hits": [{"doc_id": len(qs), "chunk_id": len(qs), "content": body + str(len(qs)), "path": ["x"]}]}
    monkeypatch.setattr(grant_search, "search", fake)
    m = drafting.Materials(Database(tmp_path / "t.db"), {"id": -1, "name": "사업 A 2024년 실적보고서", "sector": "grant"}, set(), None, None, [],
                           scope={"user": "kim"})
    got = m.library_hits({"heading": "2. 연차 계획 대비 추진 실적"}, "실적")
    plan_qs = [q for q in qs if "계획서" in q and "실적보고서" not in q]
    assert plan_qs[:2] == ["사업 A 2024년 계획서 「추진과제」", "사업 A 2024년 계획서 「차년도 사업계획」"]   # 걸린 후보에서 멈춘다
    assert len(plan_qs) == 2 and got[0]["how"].startswith("문서함 검색(같은 연차 계획서)")


def test_placeholder_heading_can_be_filled_but_real_headings_stay():
    """자리 표시 제목은 같은 번호로 채울 수 있고, 다른 제목·번호를 바꾸는 것·여러 줄·자리 표시 없는 제목은 막는다."""
    from zzaimy.app.gdocs_agent import placeholder_heading_fill as f
    assert f("1. (추진 과제 1) ○○○○", "1. (추진 과제 1) 산학연 친화형 교육환경 구축")
    assert not f("1. (추진 과제 1) ○○○○", "2. (추진 과제 2) 다른 과제")              # 번호를 바꾸지 않는다
    assert not f("1. (추진 과제 1) ○○○○", "1. (추진 과제 1) ○○○○")
    assert not f("1. (추진 과제 1) ○○○○", "1. (추진 과제 1) 과제\n본문 문장")
    assert not f("1. 추진 배경 및 필요성", "1. 추진 배경 및 필요성 — 지역 수요가 늘었다")    # 자리 표시가 없는 제목


def test_placeholder_parent_heading_is_flagged_in_materials():
    """쓰는 절의 위 제목에 자리 표시가 있으면 재료에 짚는다 — 다른 묶음(다음 과제)의 절에는 짚지 않는다."""
    from zzaimy.app import drafting
    info = {"sections": [{"index": 1, "level": 1, "start": 1, "heading": "Ⅱ. 추진 과제별 실적"},
                         {"index": 2, "level": 2, "start": 10, "heading": "1. (추진 과제 1) ○○○○"},
                         {"index": 3, "level": 3, "start": 20, "heading": "가. 추진 실적"},
                         {"index": 4, "level": 2, "start": 30, "heading": "2. 교육과정 운영 실적"},
                         {"index": 5, "level": 3, "start": 40, "heading": "가. 운영 현황"}]}
    assert drafting.placeholder_parent(info, info["sections"][2]) == "1. (추진 과제 1) ○○○○"
    assert drafting.placeholder_parent(info, info["sections"][4]) == ""
    assert "채울 자리 표시 제목" in drafting.render_materials({"placeholder": "1. (추진 과제 1) ○○○○"})


def test_fill_placeholder_heading_asks_name_and_keeps_number(monkeypatch, tmp_path):
    """위 제목의 자리 표시는 좁은 물음으로 이름만 받아 같은 번호로 채운다. 「모름」이면 그대로."""
    from types import SimpleNamespace

    from zzaimy.app import gdocs_agent

    info = {"sections": [{"index": 1, "level": 2, "start": 10, "heading": "1. (추진 과제 1) ○○○○"},
                         {"index": 2, "level": 3, "start": 20, "heading": "가. 추진 실적"}]}
    calls = []
    monkeypatch.setattr(gdocs, "replace_text", lambda acc, doc, old, new, **kw: calls.append((old, new)) or {"count": 1})

    def client(answer):
        msg = SimpleNamespace(content=answer)
        create = lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=msg)])
        return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    out = gdocs_agent.fill_placeholder_heading(client("산학연 친화형 교육환경 구축"), info, info["sections"][1], [{"op": "insert", "text": "글"}],
                                               "재료", {"account": "a", "doc": "d"}, user="u", data_dir=tmp_path)
    assert calls == [("1. (추진 과제 1) ○○○○", "1. (추진 과제 1) 산학연 친화형 교육환경 구축")] and out
    calls.clear()
    assert gdocs_agent.fill_placeholder_heading(client("모름"), info, info["sections"][1], [], "", {"account": "a", "doc": "d"},
                                                user="u", data_dir=tmp_path) == [] and calls == []
