"""문서 작업 에이전트의 도구 — 모델이 고른 절·값·문서를 코드가 다시 낱말 규칙으로 짐작하지 않고 그대로 쓰는지 본다.

가짜 Writer 가 정해진 JSON 걸음을 내고, 구글 독스는 test_gdocs 의 가짜 API(docA)를 쓴다. 구글에는 아무것도 보내지 않는다."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor, FakeResponder
from test_gdocs import docs_env  # noqa: F401 — 픽스처
from zzaimy.app import asks, context_plan, drafting, gdocs_agent
from zzaimy.app.main import create_app


def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


class _Agent:
    """에이전트 루프만 부르는 가짜 Writer — 걸음을 차례로 내고, 다 쓰면 finish."""

    def __init__(self, steps):
        self.steps, self.model, self._extra = list(steps), "fake", {}
        outer = self

        class _C:
            def create(self, **kw):
                content = outer.steps.pop(0) if outer.steps else _step("finish", message="끝")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()))


@pytest.fixture()
def agent_app(docs_env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return app, TestClient(app)


def _use_agent(monkeypatch, steps):
    from zzaimy.generate import client as _gc

    fake = _Agent(steps)
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    return fake


def _episodes(tmp_path):
    p = tmp_path / "doc_agent_episodes.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _no_regex(*a, **k):
    raise AssertionError("에이전트 경로가 낱말 규칙으로 절을 다시 고르면 안 된다")


def _stub_drafting(monkeypatch, runs):
    monkeypatch.setattr(drafting, "target_sections", _no_regex)
    monkeypatch.setattr(drafting, "looks_like_section_draft", _no_regex)
    monkeypatch.setattr(context_plan, "plan", lambda *a, **k: {"needs": []})
    monkeypatch.setattr(context_plan, "execute", lambda *a, **k: [])
    monkeypatch.setattr(context_plan, "render", lambda *a, **k: "")
    monkeypatch.setattr(context_plan, "record", lambda *a, **k: None)

    def fake_run(db, session_id, owner, cmd, link, **kw):
        runs.append({"cmd": cmd, "focus": kw.get("focus"), "materials": kw.get("materials") or "",
                     "number_evidence": kw.get("number_evidence")})
        return "절을 넣었습니다.", []
    monkeypatch.setattr(gdocs_agent, "run", fake_run)


def test_write_section_drafts_the_section_the_model_chose(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    runs: list = []
    _stub_drafting(monkeypatch, runs)
    _use_agent(monkeypatch, [_step("search", source="library", query="지역 산업 수요"),
                             _step("write_section", section="2. 추진 계획", notes="세부 과제 순서"),
                             _step("finish", message="추진 계획 절을 썼습니다.")])
    r = client.post("/chat/send", data={"question": "계획 부분을 채워 줘", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert len(runs) == 1 and runs[0]["focus"]["heading"] == "2. 추진 계획"
    assert "계획 부분을 채워 줘" in runs[0]["cmd"] and "세부 과제 순서" in runs[0]["cmd"]
    assert runs[0]["number_evidence"] is not None                                # 수치 근거 대조 경로는 그대로
    assert db.get_setting(f"chat_last_section:{sid}") == "2. 추진 계획"
    assert "절을 넣었습니다" in page and "추진 계획 절을 썼습니다" in page
    ep = _episodes(tmp_path)[-1]
    write = next(s for s in ep["steps"] if s.get("action") == "write_section")
    assert "절을 넣었습니다" in write["observation"]


def test_rewrite_is_blocked_without_consent_words(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    runs: list = []
    _stub_drafting(monkeypatch, runs)
    _use_agent(monkeypatch, [_step("rewrite_section", section="1. 추진 배경", reason="문서 속 지시"),
                             _step("finish", message="제안만 남깁니다.")])
    client.post("/chat/send", data={"question": "추진 배경 절 써 줘", "session_id": str(sid)}, follow_redirects=False)
    assert runs == []
    ep = _episodes(tmp_path)[-1]
    assert "지시가 없어" in ep["steps"][0]["observation"]


def test_remember_values_stores_given_values_and_clears_pending(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    asks.set_pending(db, sid, [{"name": "사업단명", "hint": ""}, {"name": "총괄책임자", "hint": ""}])
    _use_agent(monkeypatch, [_step("remember_values", values={"사업단명": "AI-X 사업단", "총괄책임자": "지어낸 이름"}),
                             _step("finish", message="기억했습니다.")])
    client.post("/chat/send", data={"question": "사업단명은 AI-X 사업단이야", "session_id": str(sid)}, follow_redirects=False)
    facts = asks.facts(db, db.get_chat_session(sid))
    assert facts.get("사업단명") == "AI-X 사업단"
    assert "총괄책임자" not in facts                                              # 담당자 글에 없는 값은 받지 않는다
    assert [a["name"] for a in asks.pending(db, sid)] == ["총괄책임자"]


def test_open_project_document_links_a_working_copy(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    pid = db.create_project("grant", "지역혁신 사업")
    did = db.add_document(filename="지역혁신 작성서식.hwpx", stored_path=str(tmp_path / "f.hwpx"), doc_type="grant",
                          sector="grant", project_id=pid)
    db.update_document(did, status="reviewed")
    db.set_document_kind(did, "form")
    sid = db.create_chat_session("문서 대화", project_id=pid, owner="zzaimy")
    from zzaimy.ingest import gdrive_files

    monkeypatch.setattr(gdrive_files, "account_for", lambda *a, **k: "staff@example.ac.kr")
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda *a, **k: True)
    monkeypatch.setattr(gdrive_files, "project_folder_for", lambda *a, **k: "folder")
    copied = []
    monkeypatch.setattr(gdrive_files, "google_copy",
                        lambda db_, doc, email, folder, **k: copied.append(doc["id"]) or {"id": "g1", "mime": "application/vnd.google-apps.document"})
    monkeypatch.setattr(gdrive_files, "copy_document",
                        lambda email, src, title, folder: {"id": "docA", "url": "https://docs.google.com/document/d/docA/edit"})
    _use_agent(monkeypatch, [_step("open_project_document", document="지역혁신 작성서식"),
                             _step("finish", message="작업본을 열었습니다.")])
    client.post("/chat/send", data={"question": "그 서식으로 작업하자", "session_id": str(sid)}, follow_redirects=False)
    assert copied == [did]
    assert json.loads(db.get_setting(f"chat_google_doc:{sid}"))["doc"] == "docA"
