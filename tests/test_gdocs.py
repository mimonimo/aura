"""구글 독스 문서 작업 — 가짜 독스 API 로 구조 읽기·절 삽입·치환·감사 기록·화면 경로를 검증한다. 구글에는 아무것도 보내지 않는다."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app.main import create_app
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
        {"op": "insert", "section": 2, "old": "", "text": "산학협력 수요조사에서 응답 기관의 다수가 공동 교육과정을 원했다. 문의 010-9999-8888"},
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
    assert "010-9999-8888" not in batch[-2][2]          # 넣는 글의 전화번호는 가려진다
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
