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
    # 문서에 써 넣는 글: 담당자 업무 연락처는 남기고(신청서·공문에 적어야 한다, 2026-09-24) 주민번호 같은 식별 번호만 가린다
    assert "010-9999-8888" in batch[-2][2] and "900101-1234568" not in batch[-2][2]
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
    app = _create(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                  processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    r = client.post("/chat/send", data={"question": "2027년 지역혁신 사업계획서 초안을 써 줘. 절은 셋으로."}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "드라이브에 문서 「2027년 지역혁신 사업계획서」" in page and "만들어 이 대화에 연결했습니다" in page and "적용됨" in page
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
    fills = json.loads([c for c in calls if c[1].endswith(":batchUpdate")][-1][2])["requests"]
    assert [f["insertText"]["location"]["index"] for f in fills] == [48, 45, 41, 38]      # 뒤에서부터
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
