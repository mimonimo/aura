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
    client.post("/chat/send", data={"question": "지역혁신 작성서식으로 작업하자", "session_id": str(sid)}, follow_redirects=False)
    assert copied == [did]
    assert json.loads(db.get_setting(f"chat_google_doc:{sid}"))["doc"] == "docA"


# ---------------------------------------------------------------- 보안: 값 기억·문서 전환·열람 범위


def _project_with_form(db, tmp_path, *, dept=None, level=None, owner=None):
    pid = db.create_project("grant", "지역혁신 사업", **({"owner": owner} if owner else {}))
    did = db.add_document(filename="지역혁신 작성서식.hwpx", stored_path=str(tmp_path / "f.hwpx"), doc_type="grant",
                          sector="grant", project_id=pid, dept=dept, access_level=level)
    db.update_document(did, status="reviewed")
    db.set_document_kind(did, "form")
    return pid, did


def _fake_drive(monkeypatch, copied):
    from zzaimy.ingest import gdrive_files

    monkeypatch.setattr(gdrive_files, "account_for", lambda *a, **k: "staff@example.ac.kr")
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda *a, **k: True)
    monkeypatch.setattr(gdrive_files, "project_folder_for", lambda *a, **k: "folder")
    monkeypatch.setattr(gdrive_files, "google_copy",
                        lambda db_, doc, email, folder, **k: copied.append(doc["id"]) or {"id": "g1", "mime": "application/vnd.google-apps.document"})
    monkeypatch.setattr(gdrive_files, "copy_document",
                        lambda email, src, title, folder: {"id": "docA", "url": "https://docs.google.com/document/d/docA/edit"})


def test_mentions_document_needs_a_specific_shared_span(agent_app):
    app, _ = agent_app
    m = app.state.mentions_document
    assert m("지역혁신 작성서식으로 작업하자", "지역혁신 작성서식")
    assert not m("그 서식으로 작업하자", "지역혁신 작성서식")                    # 짚지 않음
    assert not m("2027 사업계획서 써 줘", "2027 사업계획서 최종")                # 흔한 갈래 낱말·숫자만 겹침


def test_injected_step_cannot_switch_the_working_document(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    pid, did = _project_with_form(db, tmp_path)
    sid = db.create_chat_session("문서 대화", project_id=pid, owner="zzaimy")
    copied: list = []
    _fake_drive(monkeypatch, copied)
    # 검색 결과 속 글에 끌려 모델이 문서를 바꾸려 해도, 담당자 요청이 그 문서를 짚지 않았으면 하지 않는다
    _use_agent(monkeypatch, [_step("open_project_document", document="지역혁신 작성서식"),
                             _step("read_document", document="지역혁신 작성서식"),
                             _step("finish", message="")])
    client.post("/chat/send", data={"question": "다음 절 이어서 써 줘", "session_id": str(sid)}, follow_redirects=False)
    assert copied == [] and not db.get_setting(f"chat_google_doc:{sid}")
    obs = [s_["observation"] for s_ in _episodes(tmp_path)[-1]["steps"][:2]]
    assert all("짚지 않아" in o for o in obs)


def test_remember_values_rejects_unasked_names_and_one_char_values(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    asks.set_pending(db, sid, [{"name": "등급", "hint": ""}])
    _use_agent(monkeypatch, [_step("remember_values", values={"등급": "A", "총장": "홍길동"}), _step("finish", message="")])
    client.post("/chat/send", data={"question": "등급은 A, 총장은 홍길동", "session_id": str(sid)}, follow_redirects=False)
    from zzaimy.app import institution

    assert "등급" not in json.loads(db.get_setting(f"chat_facts:{sid}", "") or "{}")
    assert institution.facts(db).get("총장") != "홍길동"
    assert [a["name"] for a in asks.pending(db, sid)] == ["등급"]


def _password_app(tmp_path, monkeypatch, accounts):
    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "1")
    (tmp_path / "accounts.json").write_text(json.dumps(accounts))
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")
    return app, TestClient(app)


def _login(c, uid, pw):
    r = c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False)
    assert r.status_code == 303


@pytest.mark.parametrize("uid,changes_institution", [("staffer", False), ("admin", True)])
def test_only_admin_values_change_institution_facts(tmp_path, monkeypatch, uid, changes_institution):
    app, client = _password_app(tmp_path, monkeypatch, {
        "staffer": {"pw": "pw-staff-1", "role": "staff", "dept": "기획"},
        "admin": {"pw": "pw-admin-1", "role": "dev"}})
    _login(client, uid, f"pw-{'staff' if uid == 'staffer' else 'admin'}-1")
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner=uid)
    asks.set_pending(db, sid, [{"name": "대학명", "hint": ""}])
    _use_agent(monkeypatch, [_step("remember_values", values={"대학명": "한빛대학교"}), _step("finish", message="")])
    client.post("/chat/send", data={"question": "대학명은 한빛대학교", "session_id": str(sid)}, follow_redirects=False)
    from zzaimy.app import institution

    assert json.loads(db.get_setting(f"chat_facts:{sid}"))["대학명"] == "한빛대학교"        # 대화(프로젝트) 값으로는 늘 기억
    assert (db.get_setting("institution:대학명", "") == "한빛대학교") is changes_institution
    assert asks.pending(db, sid) == []


def test_documents_outside_scope_are_hidden_from_agent_and_fallback(tmp_path, monkeypatch):
    app, client = _password_app(tmp_path, monkeypatch, {"staffer": {"pw": "pw-staff-1", "role": "staff", "dept": "기획"}})
    _login(client, "staffer", "pw-staff-1")
    db = app.state.db
    pid, did = _project_with_form(db, tmp_path, dept="입학", level="dept", owner="staffer")   # 자기 프로젝트의 대화(남의 프로젝트는 공유받아야 쓴다)
    sid = db.create_chat_session("문서 대화", project_id=pid, owner="staffer")
    copied: list = []
    _fake_drive(monkeypatch, copied)
    fake = _use_agent(monkeypatch, [_step("open_project_document", document="지역혁신 작성서식"), _step("finish", message="")])
    seen: list = []
    orig = fake.client.chat.completions.create

    def spy(**kw):
        seen.append("\n".join(m_["content"] for m_ in kw["messages"]))
        return orig(**kw)
    fake.client.chat.completions.create = spy
    client.post("/chat/send", data={"question": "지역혁신 작성서식으로 작업하자", "session_id": str(sid)}, follow_redirects=False)
    steps = [t for t in seen if "[담당자 요청]" in t]          # 에이전트 걸음 요청만 — 답 뒤 배경의 작업 메모 갱신 호출은 대화 원문을 담는다(병렬 시험에서 먼저 잡히던 것)
    assert steps and not any("지역혁신 작성서식" in t.split("[담당자 요청]")[0] for t in steps)   # 상태에 제목이 없다
    assert copied == []
    assert app.state.project_doc_named(db.get_project(pid), sid, "지역혁신 작성서식으로 작업하자") is None


# ---------------------------------------------------------------- 검토·편집·내보내기·작업본·안내 상자 — 낱말 규칙 갈래를 지나지 않고 직접


def _linked(app):
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    return db, sid


def test_review_section_goes_straight_to_the_chosen_section(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db, sid = _linked(app)
    runs: list = []
    _stub_drafting(monkeypatch, runs)                    # target_sections·looks_like_section_draft 를 부르면 실패
    _use_agent(monkeypatch, [_step("review_section", section="2. 추진 계획"), _step("finish", message="")])
    client.post("/chat/send", data={"question": "추진 계획이 괜찮은지 봐 줘", "session_id": str(sid)}, follow_redirects=False)
    assert len(runs) == 1 and runs[0]["focus"]["heading"] == "2. 추진 계획" and runs[0]["materials"]
    assert json.loads(db.get_setting(f"chat_last_review:{sid}"))["heading"] == "2. 추진 계획"


def test_edit_document_applies_the_models_instruction(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db, sid = _linked(app)
    runs: list = []
    _stub_drafting(monkeypatch, runs)
    _use_agent(monkeypatch, [_step("edit_document", instruction="추진 배경 첫 문장을 간결하게"), _step("finish", message="")])
    client.post("/chat/send", data={"question": "배경 문구 좀 고쳐 줘", "session_id": str(sid)}, follow_redirects=False)
    assert len(runs) == 1 and runs[0]["cmd"] == "추진 배경 첫 문장을 간결하게" and runs[0]["focus"] is None


def test_export_and_new_copy_run_without_the_fallback_patterns(agent_app, tmp_path, monkeypatch):
    """요청 글이 물러난 경로의 낱말 규칙(「한글 … 내보내」·「작업본 새로」)에 맞지 않아도 모델이 고른 도구가 그대로 돈다."""
    app, client = agent_app
    db, sid = _linked(app)
    _use_agent(monkeypatch, [_step("export_hwp", remove_boxes=False)])
    r = client.post("/chat/send", data={"question": "완성본 내보내 줘", "session_id": str(sid)}, follow_redirects=False)
    assert "한글 완성본을 만들 수 없습니다" in client.get(r.headers["location"]).text     # 원본 서식 없음 — _export_hwp 가 돌았다
    _use_agent(monkeypatch, [_step("new_work_copy")])
    r = client.post("/chat/send", data={"question": "작업본을 최신 변환으로 바꿔 줘", "session_id": str(sid)}, follow_redirects=False)
    assert "새 작업본을 만들 수 없습니다" in client.get(r.headers["location"]).text


def test_guide_boxes_are_counted_first_and_removed_only_after_confirmation(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db, sid = _linked(app)
    from zzaimy.ingest import gdocs

    calls: list = []
    monkeypatch.setattr(gdocs, "remove_instruction_boxes", lambda *a, dry_run=False, **k: calls.append(dry_run) or {"count": 3})
    _use_agent(monkeypatch, [_step("remove_guide_boxes")])
    client.post("/chat/send", data={"question": "안내 상자 지워 줘", "session_id": str(sid)}, follow_redirects=False)
    assert calls == [True]                                                        # 처음엔 세기만
    _use_agent(monkeypatch, [_step("remove_guide_boxes")])
    client.post("/chat/send", data={"question": "안내 상자 삭제 확인, 지워 줘", "session_id": str(sid)}, follow_redirects=False)
    assert calls == [True, False]


# ---------------------------------------------------------------- 질문 답 — 에이전트가 찾은 근거를 버리지 않는다


def test_answer_question_uses_the_evidence_the_agent_found(tmp_path, monkeypatch):
    from zzaimy.app.responder import NO_EVIDENCE_NOTE, AgentResponder
    from zzaimy.generate import client as _gc

    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=AgentResponder())
    client = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("공고 질문", owner="zzaimy")
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [
        {"title": "부트캠프 공고", "text": "본신청 접수 7.28.~8.6. 신청 자격: 재학생", "doc_id": None, "note": ""}])
    steps = [_step("search", source="library", query="본신청 마감일"),
             _step("answer_question", question="신청 자격과 본신청 마감일은?")]
    prompts: list = []

    class _Fake:
        model, _extra = "fake", {}

        def __init__(self):
            outer = self

            class _C:
                def create(self, **kw):
                    first = kw["messages"][0]["content"]
                    if first.startswith("당신은 대학 행정 문서 작성 에이전트다"):
                        content = steps.pop(0) if steps else _step("finish", message="")
                    else:
                        prompts.append(kw["messages"][-1]["content"])
                        content = "본신청 마감은 8.6.입니다(《부트캠프 공고》)."
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])
            outer.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()))

    fake = _Fake()
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    r = client.post("/chat/send", data={"question": "신청 자격과 본신청 마감일만 공고 기준으로 알려 줘. 문서는 고치지 말고.",
                                        "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert prompts and "본신청 접수 7.28.~8.6." in prompts[0] and "《부트캠프 공고》" in prompts[0]
    assert NO_EVIDENCE_NOTE.strip() not in prompts[0]                              # 「근거 없음」 길로 가지 않는다
    assert "본신청 마감은 8.6." in page and "부트캠프 공고" in page                     # 출처 목록에도 선다


# ---------------------------------------------------------------- 보안: 직접 부르는 함수의 열람 범위·검토의 쓰기


def test_agent_review_never_writes_to_the_document(agent_app, tmp_path, monkeypatch):
    """검토는 동의 관문이 없는 도구라 편집 계획이 나와도 보류만 한다(담당자가 적용해야 쓴다)."""
    app, client = agent_app
    db, sid = _linked(app)
    seen: list = []
    monkeypatch.setattr(context_plan, "plan", lambda *a, **k: {"needs": []})
    monkeypatch.setattr(gdocs_agent, "run", lambda db_, s_, o_, cmd, link, **kw: seen.append(kw.get("confirm")) or ("검토 의견", []))
    _use_agent(monkeypatch, [_step("review_section", section="2. 추진 계획"), _step("finish", message="")])
    client.post("/chat/send", data={"question": "추진 계획 봐 줘", "session_id": str(sid)}, follow_redirects=False)
    assert seen == [True]


def test_scope_applies_to_criteria_working_copy_source_and_work_on_route(tmp_path, monkeypatch):
    app, client = _password_app(tmp_path, monkeypatch, {"staffer": {"pw": "pw-staff-1", "role": "staff", "dept": "기획"}})
    _login(client, "staffer", "pw-staff-1")
    db = app.state.db
    pid, form_id = _project_with_form(db, tmp_path, dept="입학", level="dept")
    crit = db.add_document(filename="입학 공고.pdf", stored_path=str(tmp_path / "c.pdf"), doc_type="grant", dept="입학", access_level="dept")
    open_crit = db.add_document(filename="공개 공고.pdf", stored_path=str(tmp_path / "o.pdf"), doc_type="grant", access_level="public")
    db.set_project_criteria(pid, [crit, open_crit])
    sid = db.create_chat_session("문서 대화", project_id=pid, owner="staffer")
    assert app.state.scoped_criteria(sid) == [open_crit]                           # 범위 밖 기준 문서는 재료·근거에 들지 않는다
    # 문서함의 「이 문서로 작업」 경로도 범위 밖 문서는 없는 것과 같다
    copied: list = []
    _fake_drive(monkeypatch, copied)
    r = client.post(f"/api/chat/{sid}/work-on/{form_id}")
    assert r.status_code == 404 and copied == []
    # 한글 내보내기·새 작업본의 원본 서식도 범위 밖 양식은 고르지 않는다
    db.add_file("google", "https://docs.google.com/document/d/docA/edit", name="작업본", session_id=sid, doc_id=form_id)
    assert app.state.working_copy_source(sid, db.get_chat_session(sid), "내보내 줘", "한글 완성본을 만들") is None


def test_must_change_account_cannot_use_chat_or_other_posts(tmp_path, monkeypatch):
    monkeypatch.delenv("ZZAIMY_DEV_PASSWORD", raising=False)
    create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    dev_pw = (tmp_path / "initial_dev_password.txt").read_text().strip()
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder(), password="boot-pass-1")
    c = TestClient(app)
    _login(c, "zzdev", dev_pw)
    n = len(app.state.db.list_chat_sessions()) if hasattr(app.state.db, "list_chat_sessions") else None
    r = c.post("/chat/send", data={"question": "안녕"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings")
    if n is not None:
        assert len(app.state.db.list_chat_sessions()) == n
    r = c.post("/api/chat-documents/create", data={"title": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings")


# ---------------------------------------------------------------- 찾은 문서 이어 읽기(read_found) · 걸음 한도 뒤 답하기


_KPI_ROWS = [f"| 가상지표 {i:03d} | 목표 {i * 7}건 |" for i in range(1, 101)]    # 한 조각이 관찰 한도를 넘는 표
_TITLE = "가상 사업 2차년도 계획서"


def _kpi_doc(db, tmp_path, *, owner=None, level=None, title=_TITLE):
    did = db.add_document(filename=f"{title}.hwpx", stored_path=str(tmp_path / f"{title}.hwpx"), doc_type="grant",
                          sector="grant", access_level=level, **({"owner": owner} if owner else {}))
    db.update_document(did, status="reviewed")
    db.replace_doc_chunks(did, [{"kind": "text", "content": "1. 사업 개요\n가상 사업의 추진 배경"},
                                {"kind": "text", "content": "핵심 성과지표 총괄표\n" + "\n".join(_KPI_ROWS)},
                                {"kind": "text", "content": "3. 예산 계획\n가상 예산 표"}])
    return did, db.list_doc_chunks(did)[1]["id"]


def _truncated_hit(db, did, chunk_id):
    text = db.list_doc_chunks(did)[1]["content"]
    return {"title": _TITLE, "text": text[:600], "doc_id": did, "chunk_id": chunk_id, "note": ""}


class _WriterAndAnswer:
    """에이전트 걸음은 정해진 JSON 으로, 답변기 호출은 받은 프롬프트를 남기고 정해진 답으로."""

    model, _extra = "fake", {}

    def __init__(self, steps, answer=f"가상지표 목록을 정리했습니다(《{_TITLE}》)."):
        self.steps, self.prompts, self.agent_msgs = list(steps), [], []
        outer = self

        class _C:
            def create(self, **kw):
                if kw["messages"][0]["content"].startswith("당신은 대학 행정 문서 작성 에이전트다"):
                    outer.agent_msgs.append(kw["messages"])
                    content = outer.steps.pop(0) if outer.steps else _step("finish", message="")
                else:
                    outer.prompts.append(kw["messages"][-1]["content"])
                    content = answer
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()))


def _answer_app(tmp_path, monkeypatch):
    from zzaimy.app.responder import AgentResponder

    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=AgentResponder())
    return app, TestClient(app)


def test_read_found_continues_a_truncated_table_and_answers_from_it(tmp_path, monkeypatch):
    from zzaimy.app import doc_agent
    from zzaimy.generate import client as _gc

    app, client = _answer_app(tmp_path, monkeypatch)
    db = app.state.db
    did, cid = _kpi_doc(db, tmp_path)
    sid = db.create_chat_session("지표 질문", owner="zzaimy")
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [_truncated_hit(db, did, cid)])
    # 첫 이어 읽기가 알려 줄 다음 위치 — 모델은 결과 끝의 「다음 위치」 를 그대로 인자로 낸다
    texts = [c["content"] for c in db.list_doc_chunks(did)]
    _t, _e, nxt = doc_agent.read_window(texts, 1, 0)
    fake = _WriterAndAnswer([_step("search", source="library", query="핵심 성과지표 목표값"),
                             _step("read_found", document=_TITLE),
                             _step("read_found", document=str(did), **{"from": nxt[0] + 1, "line": nxt[1] + 1}),
                             _step("answer_question", question="핵심 성과지표와 목표값은?")])
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    r = client.post("/chat/send", data={"question": f"{_TITLE}의 핵심 성과지표와 목표값 알려 줘. 출처도",
                                        "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    ep = _episodes(tmp_path)[-1]
    assert [s_["action"] for s_ in ep["steps"]] == ["search", "read_found", "read_found", "answer_question"]
    assert ep["stop"] == "finished"
    assert f"(문서 {did})" in ep["steps"][0]["observation"] and "read_found" in ep["steps"][0]["observation"]
    obs1, obs2 = ep["steps"][1]["observation"], ep["steps"][2]["observation"]
    assert "가상지표 001" in obs1 and f"다음 위치: read_found {{document: {did}, from: {nxt[0] + 1}" in obs1
    assert all(ln in obs1 + obs2 for ln in _KPI_ROWS) and "문서 끝" in obs2      # 표 전체를 두 번에 나눠, 줄을 자르지 않고
    assert fake.prompts and "| 가상지표 100 | 목표 700건 |" in fake.prompts[0]     # 잘렸던 표 끝이 답의 근거에 들어감
    assert "가상지표 목록을 정리했습니다" in page and _TITLE in page                     # 답과 출처 목록


def test_read_found_refuses_documents_that_were_not_in_the_results(agent_app, tmp_path, monkeypatch):
    app, client = agent_app
    db = app.state.db
    did, cid = _kpi_doc(db, tmp_path)
    other, _ = _kpi_doc(db, tmp_path, title="가상 비밀 메모")
    db.replace_doc_chunks(other, [{"kind": "text", "content": "결과에 없던 가상 문서 본문 ZQX"}])
    sid = db.create_chat_session("지표 질문", owner="zzaimy")
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [_truncated_hit(db, did, cid)])
    _use_agent(monkeypatch, [_step("read_found", document=_TITLE),                     # 찾기 전 — 읽을 문서 없음
                             _step("search", source="library", query="핵심 성과지표"),
                             _step("read_found", document="가상 비밀 메모"),
                             _step("read_found", document=str(other)),
                             _step("finish", message="끝")])
    client.post("/chat/send", data={"question": "핵심 성과지표 알려 줘", "session_id": str(sid)}, follow_redirects=False)
    steps = _episodes(tmp_path)[-1]["steps"]
    assert "search 로 찾은 문서가 없다" in steps[0]["observation"]
    for s_ in steps[2:4]:
        assert "결과에 나온 문서 하나를" in s_["observation"] and "ZQX" not in s_["observation"]


def test_read_found_rechecks_the_viewer_scope(tmp_path, monkeypatch):
    app, client = _password_app(tmp_path, monkeypatch, {"staffer": {"pw": "pw-staff-1", "role": "staff", "dept": "기획"}})
    _login(client, "staffer", "pw-staff-1")
    db = app.state.db
    did, cid = _kpi_doc(db, tmp_path, owner="someone", level="owner")
    sid = db.create_chat_session("지표 질문", owner="staffer")
    # 검색 결과에 범위 밖 문서가 섞여 들어왔다고 가정해도(그 사이 권한이 바뀐 경우 등) 이어 읽기는 범위를 다시 본다
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [_truncated_hit(db, did, cid)])
    _use_agent(monkeypatch, [_step("search", source="library", query="핵심 성과지표"),
                             _step("read_found", document=_TITLE), _step("finish", message="끝")])
    client.post("/chat/send", data={"question": "핵심 성과지표 알려 줘", "session_id": str(sid)}, follow_redirects=False)
    obs = _episodes(tmp_path)[-1]["steps"][1]["observation"]
    assert "열람 범위 밖" in obs and "가상지표 001" not in obs


def test_step_limit_answers_from_the_evidence_found_so_far(tmp_path, monkeypatch):
    import functools

    from zzaimy.app import doc_agent
    from zzaimy.generate import client as _gc

    app, client = _answer_app(tmp_path, monkeypatch)
    db = app.state.db
    did, cid = _kpi_doc(db, tmp_path)
    sid = db.create_chat_session("지표 질문", owner="zzaimy")
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [_truncated_hit(db, did, cid)])
    monkeypatch.setattr(doc_agent, "run", functools.partial(doc_agent.run, max_steps=2))
    fake = _WriterAndAnswer([_step("search", source="library", query="핵심 성과지표"),
                             _step("search", source="library", query="성과지표 목표값"),
                             _step("answer_question", question="핵심 성과지표와 목표값은?")])
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    r = client.post("/chat/send", data={"question": "핵심 성과지표와 목표값 알려 줘", "session_id": str(sid)}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    ep = _episodes(tmp_path)[-1]
    assert ep["stop"] == "finished" and [s_["action"] for s_ in ep["steps"]] == ["search", "search", "answer_question"]
    assert "걸음 한도(2)" in fake.agent_msgs[-1][-1]["content"]
    assert "가상지표 목록을 정리했습니다" in page and "요청을 끝내지 못했습니다" not in page
    assert fake.prompts and "가상지표 001" in fake.prompts[0]


def test_section_writer_sees_values_already_being_asked(agent_app, tmp_path, monkeypatch):
    """절 작성 모델은 이미 묻고 있는 값 이름을 본다 — 같은 뜻의 빈 값을 새 이름으로 또 묻지 않게(합치는 판단은 모델)."""
    app, client = agent_app
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))
    asks.set_pending(db, sid, [{"name": "가상 취업 실적(최근 3년)", "hint": ""}])
    runs: list = []
    _stub_drafting(monkeypatch, runs)
    _use_agent(monkeypatch, [_step("write_section", section="2. 추진 계획"), _step("finish", message="썼습니다.")])
    client.post("/chat/send", data={"question": "계획 부분을 채워 줘", "session_id": str(sid)}, follow_redirects=False)
    assert runs and "[이미 담당자에게 묻고 있는 값]\n- 가상 취업 실적(최근 3년)" in runs[0]["materials"]
    assert "같은 뜻의 값이 있으면 asks 에 새 이름을 만들지 말고" in gdocs_agent._PROMPT



def test_read_found_stops_after_per_document_budget(agent_app, tmp_path, monkeypatch):
    """한 요청에서 한 문서를 READ_PER_DOC 번 넘게 이어 읽지 않는다 — 조각을 하나씩 넘기며 걸음을 다 쓰지 않게."""
    from zzaimy.app import doc_agent as da
    app, client = agent_app
    db = app.state.db
    did, cid = _kpi_doc(db, tmp_path)
    sid = db.create_chat_session("지표 질문", owner="zzaimy")
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [_truncated_hit(db, did, cid)])
    reads = [_step("read_found", document=str(did), around=f"가상지표 {i:03d}") for i in range(1, da.READ_PER_DOC + 2)]
    _use_agent(monkeypatch, [_step("search", source="library", query="핵심 성과지표")] + reads + [_step("finish", message="끝")])
    client.post("/chat/send", data={"question": "핵심 성과지표 알려 줘", "session_id": str(sid)}, follow_redirects=False)
    steps = [s_ for s_ in _episodes(tmp_path)[-1]["steps"] if s_.get("action") == "read_found"]
    assert len(steps) == da.READ_PER_DOC + 1
    assert all("가상지표" in s_["observation"] for s_ in steps[:da.READ_PER_DOC])
    assert "이미 4번 읽었다" in steps[-1]["observation"] and "write_section" in steps[-1]["observation"]
