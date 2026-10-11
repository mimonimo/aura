"""문맥 예산 — 답변·에이전트 경로의 요청이 서빙 모델의 max_model_len 안에서만 나가는지, 도구 실패·질문 요청에서
낱말 규칙 대비 경로가 문서 갈래로 가지 않는지, 근거 없는 답을 막는지 본다(가짜 클라이언트, 서버 없이)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import doc_agent, llm_budget
from zzaimy.app.main import create_app


@pytest.fixture(autouse=True)
def _fresh_cache():
    llm_budget.reset_cache()
    yield
    llm_budget.reset_cache()


class _Fake:
    """vLLM 처럼 /v1/models 에 max_model_len 을 싣는 가짜. 보낸 요청은 sent 에 남는다."""

    def __init__(self, max_len=None, replies=None, model="m"):
        self.model, self._extra, self.sent = model, {}, []
        outer = self
        data = [SimpleNamespace(id=model, max_model_len=max_len)] if max_len else []

        class _Models:
            calls = 0

            def list(self):
                _Models.calls += 1
                if max_len is None:
                    raise ConnectionError("서버 없음")
                return SimpleNamespace(data=data)

        class _C:
            def create(self, **kw):
                outer.sent.append(kw)
                content = replies.pop(0) if replies else "답"
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])

        self.models = _Models
        self.client = SimpleNamespace(models=_Models(), chat=SimpleNamespace(completions=_C()))


def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


# ---------------------------------------------------------------- 한도 읽기·어림·줄이기


def test_max_model_len_is_read_from_server_and_cached():
    fake = _Fake(max_len=8192)
    assert llm_budget.context_len(fake) == 8192
    assert llm_budget.context_len(fake) == 8192
    assert fake.models.calls == 1                                       # 연결마다 한 번


def test_max_model_len_falls_back_to_configured_default(monkeypatch):
    monkeypatch.setenv("ZZAIMY_WRITER_CONTEXT", "12000")
    assert llm_budget.context_len(_Fake(max_len=None)) == 12000
    plain = SimpleNamespace(model="m", client=SimpleNamespace())          # models 가 없는 클라이언트(시험용 가짜 등)
    assert llm_budget.context_len(plain) == 12000


def test_model_extra_field_is_read():
    m = SimpleNamespace(id="m", model_extra={"max_model_len": 4096})
    fake = SimpleNamespace(model="m", client=SimpleNamespace(models=SimpleNamespace(list=lambda: SimpleNamespace(data=[m]))))
    assert llm_budget.context_len(fake) == 4096


def test_korean_estimate_is_not_below_one_token_per_syllable():
    text = "본신청 접수 기간은 칠월 이십팔일부터 팔월 육일까지이다"
    hangul = sum(1 for ch in text if "가" <= ch <= "힣")
    assert llm_budget.estimate_tokens(text) >= hangul


def test_oversized_prompt_is_trimmed_below_limit_keeping_request_and_evidence():
    fake = _Fake(max_len=6000)
    question = "신청 자격과 본신청 마감일은?"
    evidence = "[이미 찾은 근거]\n《모집 공고》 본신청 접수 7.28.~8.6."
    attachment = "\n\n[첨부 문서 본문]\n" + "연결 문서의 긴 글 한 줄입니다.\n" * 900
    history = [{"role": "user", "content": "앞 대화 " * 400}, {"role": "assistant", "content": "앞 답 " * 400}]
    msgs = [{"role": "system", "content": "시스템"}, *history, {"role": "user", "content": question + attachment + "\n\n" + evidence}]
    cuts = [llm_budget.Cut(msg=-1, text=attachment), llm_budget.Cut(msg=1), llm_budget.Cut(msg=2), llm_budget.OUTPUT,
            llm_budget.Cut(msg=-1, text=evidence)]
    fitted, mt = llm_budget.fit_messages(fake, msgs, 3072, cuts, floor=1024)
    assert llm_budget.estimate_messages(fitted) <= 6000 - mt
    assert mt >= 1024
    last = fitted[-1]["content"]
    assert last.startswith(question) and evidence in last                # 요청과 에이전트 근거는 그대로
    assert "일부만 실었다" in last                                       # 첨부는 줄였다고 밝힌다


def test_history_goes_before_output_budget_and_short_prompt_is_untouched():
    fake = _Fake(max_len=6000)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "짧은 질문"}]
    assert llm_budget.fit_messages(fake, msgs, 3072) == (msgs, 3072)
    big = [{"role": "system", "content": "s"}, {"role": "user", "content": "옛 대화 " * 1500}, {"role": "user", "content": "질문"}]
    fitted, mt = llm_budget.fit_messages(fake, big, 3072, [llm_budget.Cut(msg=1)])
    assert [m["content"] for m in fitted] == ["s", "질문"] and mt == 3072


def test_unfittable_request_is_never_sent():
    fake = _Fake(max_len=2000)
    msgs = [{"role": "user", "content": "줄일 수 없는 지시 " * 800}]
    with pytest.raises(llm_budget.ContextOverflow):
        llm_budget.create_fitted(fake, msgs, 1024)
    assert fake.sent == []


def test_server_overflow_error_calibrates_and_retries_once():
    fake = _Fake(max_len=16384)
    calls = []

    class _E(Exception):
        status_code = 400

    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise _E("This model's maximum context length is 16384 tokens. However, you requested 3072 output tokens "
                     "and your prompt contains at least 15000 input tokens")
        return "ok"
    fake.client.chat.completions.create = create
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "옛 대화 " * 2000}, {"role": "user", "content": "질문"}]
    assert llm_budget.create_fitted(fake, msgs, 3072, [llm_budget.Cut(msg=1)]) == "ok"
    assert len(calls) == 2 and len(calls[1]["messages"]) < len(calls[0]["messages"])


def test_responder_answer_fits_the_served_model(tmp_path, monkeypatch):
    from zzaimy.app.responder import AgentResponder
    from zzaimy.generate import client as _gc

    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder())
    db = app.state.db
    sid = db.create_chat_session("질문", owner="zzaimy")
    for i in range(6):
        db.add_chat(sid, "user" if i % 2 == 0 else "assistant", "지난 대화에서 본 날짜 7.28.~8.6. " * 120)
    fake = _Fake(max_len=6000)
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    r = AgentResponder()
    r.answer(db, "본신청 마감일은?", attachment_text="연결 문서 본문 줄\n" * 3000, session_id=sid,
             evidence=[{"title": "모집 공고", "text": "본신청 접수 7.28.~8.6."}])
    req = fake.sent[0]
    assert llm_budget.estimate_messages(req["messages"]) + req["max_tokens"] <= 6000
    last = req["messages"][-1]["content"]
    assert last.startswith("본신청 마감일은?") and "본신청 접수 7.28.~8.6." in last
    assert "맥락일 뿐 근거가 아닙니다" in req["messages"][0]["content"]   # 앞 대화는 근거가 아니라는 규칙


# ---------------------------------------------------------------- 에이전트 루프: 실패·근거·검색 단서


def test_agent_loop_requests_fit_the_context():
    big_state = "최근 대화:\n" + "  [user] 긴 앞 대화 한 줄\n" * 2000
    fake = _Fake(max_len=4000, replies=[_step("finish", message="끝")])
    tools = [doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]
    ep = doc_agent.run(fake, "요청", lambda: big_state, tools)
    req = fake.sent[0]
    assert ep.finished and llm_budget.estimate_messages(req["messages"]) + req["max_tokens"] <= 4000
    assert "[담당자 요청]\n요청" in req["messages"][-1]["content"]


def test_final_tool_failure_returns_observation_and_lets_model_retry():
    tries = []

    def answer(a):
        tries.append(a)
        if len(tries) == 1:
            raise RuntimeError("일시 실패")
        return "답했다"
    tools = [doc_agent.Tool("search", "찾기", lambda a: "결과", gathers=True),
             doc_agent.Tool("answer_question", "답", answer, final=True, needs_evidence=True),
             doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    fake = _Fake(max_len=16384, replies=[_step("search", query="마감"), _step("answer_question", question="q"),
                                          _step("answer_question", question="q")])
    ep = doc_agent.run(fake, "마감일 알려 줘", lambda: "s", tools)
    assert ep.finished and ep.final_message == "답했다" and len(tries) == 2
    assert "도구 실패" in ep.steps[1]["observation"] and "다시" in ep.steps[1]["observation"]
    assert ep.failures[0]["action"] == "answer_question" and not ep.may_fall_back


def test_tool_failures_until_step_limit_do_not_allow_fallback():
    def boom(a):
        raise RuntimeError("계속 실패")
    tools = [doc_agent.Tool("answer_question", "답", boom, final=True),
             doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    fake = _Fake(max_len=16384, replies=[_step("answer_question", question="q", facts=False)] * 3)
    ep = doc_agent.run(fake, "질문", lambda: "s", tools, max_steps=3)
    assert ep.stop == "steps" and not ep.may_fall_back
    msg = doc_agent.failure_message(ep)
    assert "답변 작성 중" in msg and "문서는 바꾸지 않았습니다" in msg


def test_unreachable_model_and_format_errors_allow_fallback():
    class _Down:
        model, _extra = "m", {}
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: (_ for _ in ()).throw(ConnectionError("끊김")))))
    tools = [doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    assert doc_agent.run(_Down(), "q", lambda: "s", tools).may_fall_back
    assert doc_agent.run(_Fake(max_len=16384, replies=["x", "y"]), "q", lambda: "s", tools).may_fall_back

    class _E(Exception):
        status_code = 400
    rejected = _Fake(max_len=16384)
    rejected.client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(_E("bad"))
    assert not doc_agent.run(rejected, "q", lambda: "s", tools).may_fall_back


def test_answer_without_search_in_this_run_is_asked_to_search_first_once():
    answered = []
    tools = [doc_agent.Tool("search", "찾기", lambda a: "결과", gathers=True),
             doc_agent.Tool("answer_question", "답", lambda a: answered.append(a) or "답", final=True, needs_evidence=True)]
    fake = _Fake(max_len=16384, replies=[_step("answer_question", question="마감일은?"), _step("answer_question", question="마감일은?")])
    ep = doc_agent.run(fake, "마감일만 다시 알려 줘", lambda: "최근 대화: 마감 8.6.", tools)
    assert ep.steps[0]["observation"] == doc_agent.SEARCH_FIRST and ep.finished and len(answered) == 1   # 한 번만 막는다
    fake = _Fake(max_len=16384, replies=[_step("answer_question", question="안녕?", facts=False)])
    assert doc_agent.run(fake, "안녕", lambda: "s", tools).steps[0]["observation"] == "답"           # 사실을 묻지 않는 질문


def test_match_hint_lists_query_nouns_missing_from_hits():
    nouns = lambda q: {w for w in q.split() if len(w) >= 2}   # noqa: E731 — 형태소 분석기 대신
    hint = doc_agent.match_hint("신청 자격 본신청 마감일", ["신청 자격: 재학생"], nouns)
    assert "있음 「신청」「자격」" in hint and "「본신청」「마감일」" in hint and "다시 찾는다" in hint
    assert "없음" not in doc_agent.match_hint("신청 자격", ["신청 자격: 재학생"], nouns)


def test_negated_consent_is_not_consent():
    assert not doc_agent.consented(r"고쳐|고치|수정|정리", "문서는 고치지 말고 알려 줘")
    assert not doc_agent.consented(r"정리", "정리하지 않고 그대로 알려 줘")
    assert doc_agent.consented(r"고쳐|수정", "2절 표를 고쳐 줘")


# ---------------------------------------------------------------- 앱: 도구 실패·질문 요청의 대비 경로


class _AgentFake:
    def __init__(self, steps):
        self.steps, self.model, self._extra = list(steps), "fake", {}
        outer = self

        class _C:
            def create(self, **kw):
                content = outer.steps.pop(0) if outer.steps else _step("finish", message="")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()))


class _FailingResponder(FakeResponder):
    def __init__(self):
        self.calls = 0

    def answer(self, *a, **k):
        self.calls += 1
        raise RuntimeError("maximum context length is 16384 tokens")


def test_final_tool_failure_does_not_run_the_legacy_pipeline(tmp_path, monkeypatch):
    from zzaimy.app import drafting
    from zzaimy.generate import client as _gc

    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "1")
    resp = _FailingResponder()
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=resp)
    client = TestClient(app)
    sid = app.state.db.create_chat_session("공고 질문", owner="zzaimy")
    monkeypatch.setattr(drafting.Materials, "search_library", lambda self, query, k=4: [
        {"title": "모집 공고", "text": "신청 자격: 재학생", "doc_id": None, "note": ""}])
    fake = _AgentFake([_step("search", source="library", query="신청 자격"), _step("answer_question", question="자격은?"),
                       _step("finish", message="")])
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    r = client.post("/chat/send", data={"question": "신청 자격만 알려 줘. 문서는 고치지 말고.", "session_id": str(sid)},
                    follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert resp.calls == 1                                              # 대비 경로가 다시 답하지 않았다
    assert "요청을 끝내지 못했습니다" in page and "문서는 바꾸지 않았습니다" in page
    ep = [json.loads(x) for x in (tmp_path / "doc_agent_episodes.jsonl").read_text(encoding="utf-8").splitlines()][-1]
    obs = next(s for s in ep["steps"] if s.get("action") == "answer_question")["observation"]
    assert "도구 실패" in obs and "다시" in obs
    search_obs = next(s for s in ep["steps"] if s.get("action") == "search")["observation"]
    assert search_obs.startswith("검색어 일치")


def test_question_with_unreachable_model_takes_only_the_answer_branch(tmp_path, monkeypatch):
    from test_gdocs import docs_env  # noqa: F401
    from zzaimy.generate import client as _gc

    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder())
    client = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("문서 대화", owner="zzaimy")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "docA", "account": "staff@example.ac.kr"}))

    def down(*a, **k):
        raise ConnectionError("모델 서버 없음")
    monkeypatch.setattr(_gc, "VllmClient", down)
    from zzaimy.app import chat_documents
    monkeypatch.setattr(chat_documents, "material", lambda *a, **k: "[연결 문서] 본문")
    r = client.post("/chat/send", data={"question": "신청 자격과 마감일만 다시 알려 줘. 문서는 고치지 말고.", "session_id": str(sid)},
                    follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "합성 답변" in page                                          # 답 갈래만
    assert app.state.db.get_setting(f"chat_last_section:{sid}", "") in ("", None)
    assert "문서는 바꾸지 않았습니다" not in page                       # 연결 문서 편집 갈래(_edit_linked_doc)로 가지 않았다
    # 같은 상황에서 고치라는 요청은 편집 갈래로 간다(관문이 질문만 막는지)
    r = client.post("/chat/send", data={"question": "2절 표를 고쳐 줘", "session_id": str(sid)}, follow_redirects=False)
    assert "문서는 바꾸지 않았습니다" in client.get(r.headers["location"]).text
