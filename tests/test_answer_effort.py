"""답변 깊이(app/effort) — 입력줄의 「빠르게」·「꼼꼼하게」 두 단계. 계정마다 기억하고, 질문마다 남긴 값이 답 만들기의
모델 호출(생각 켜기·생각 몫)·작업 몫·자기 점검·걸음 한도에 닿는다. 값을 정하지 않은 호출은 지금과 같다. 학생은 고르기가 없다."""
from types import SimpleNamespace as NS

from tests.test_chat_answer_mode import _wait, staff  # noqa: F401 — 같은 시험 앱
from tests.test_model_swap import _client, _ok
from zzaimy.app import delegate, doc_agent, effort, llm_budget, responder


def test_effort_is_remembered_per_account(staff):  # noqa: F811
    app, r, login = staff
    kim = login("kim", "kim-pass")
    page = kim.get("/chat").text
    assert 'id="effortField" value="deep"' in page and '<span id="effortLabel">꼼꼼하게</span>' in page
    assert ">꼼꼼하게</b>" in page and 'data-aura="answer-effort"' in page
    got = kim.post("/api/chat/answer-mode", data={"effort": "deep"}).json()
    assert got["effort"] == "deep" and got["effort_label"] == "꼼꼼하게" and got["mode"] == "rag"
    page = login("kim", "kim-pass").get("/chat").text                    # 새 로그인(다른 기기)에서도
    assert 'id="effortField" value="deep"' in page and '<span id="effortLabel">꼼꼼하게</span>' in page
    assert 'id="effortField" value="deep"' in login("zzdev", "dev-pass").get("/chat").text   # 다른 계정은 그대로
    kim.post("/api/chat/answer-mode", data={"effort": "max"})             # 모르는 값은 무시
    assert kim.post("/api/chat/answer-mode", data={"mode": "model"}).json()["effort"] == "deep"   # 방식만 바꿔도 깊이는 그대로


def test_student_has_no_selector(staff):  # noqa: F811
    app, r, login = staff
    stu = login("stu", "stu-pass")
    assert 'id="effortPick"' not in stu.get("/chat").text
    assert stu.post("/api/chat/answer-mode", data={"effort": "deep"}).status_code == 403


def test_send_carries_effort_to_the_answer_and_tags_only_non_default(staff, monkeypatch):  # noqa: F811
    app, r, login = staff
    seen = []
    orig = r.answer

    def answer(db, question, scope=None, **kw):
        seen.append((effort.current(), responder.answer_think(), llm_budget.work_len(NS(work_context=0))))
        return orig(db, question, scope=scope, **kw)
    r.answer = answer
    monkeypatch.setattr(llm_budget, "context_len", lambda c: 262144)
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    kim = login("kim", "kim-pass")
    kim.post("/chat/send", data={"question": "빠른 질문", "effort": "fast"}, follow_redirects=False)
    assert _wait(lambda: len(seen) == 1)
    assert seen[0] == ("fast", False, 262144)                             # 빠르게: 생각만 끔, 문맥은 꼼꼼하게와 같다(10-27)
    kim.post("/api/chat/answer-mode", data={"effort": "deep"})
    kim.post("/chat/send", data={"question": "꼼꼼한 질문"}, follow_redirects=False)   # 폼 값이 없으면 계정 값
    assert _wait(lambda: len(seen) == 2)
    assert seen[1] == ("deep", True, 262144)                              # 꼼꼼하게: 생각 켬, 문맥 한도 그대로
    pages = [kim.get(u).text for u in {f"/chat/{s['id']}" for s in _sessions(app, "kim")}]
    tagged = [p for p in pages if "· 빠르게</span>" in p]
    assert len(tagged) == 1, [p.split("chat-message-mode")[1:3] for p in pages]
    assert "빠른 질문" in tagged[0]
    assert all("· 꼼꼼하게</span>" not in p for p in pages)                       # 기본(꼼꼼하게)은 붙이지 않는다


def _sessions(app, owner):
    with app.state.db._conn() as conn:
        return [{"id": r[0]} for r in conn.execute("SELECT id FROM chat_sessions WHERE owner = ?", (owner,)).fetchall()]


def test_unset_effort_changes_nothing(monkeypatch):
    assert effort.current() == "" and not effort.thinking() and effort.work_cap() == 0
    assert effort.self_check() and effort.steps(14) == 14
    monkeypatch.setattr(llm_budget, "context_len", lambda c: 262144)
    monkeypatch.delenv("ZZAIMY_WORK_CONTEXT", raising=False)
    assert llm_budget.work_len(NS(work_context=0)) == 262144
    with effort.use("fast"):
        assert llm_budget.work_len(NS(work_context=0)) == 262144 and not effort.self_check() and effort.steps(14) == 14
        assert llm_budget.work_len(NS(work_context=16000)) == 16000        # 운영 상한이 더 작으면 그것
    with effort.use("deep"):
        assert llm_budget.work_len(NS(work_context=0)) == 262144 and effort.self_check() and effort.steps(14) == 21
    with effort.use("이상한 값"):
        assert effort.current() == effort.DEFAULT == "deep"


def test_client_turns_thinking_on_only_for_deep(monkeypatch, tmp_path):
    """생각 켜기는 연결의 think_extra 로(모델 이름 없이), 출력 한도에 생각 몫을 더하고 시간 제한을 준다. 빠르게·정하지 않음은 지금 그대로."""
    c, seen = _client(monkeypatch, tmp_path, lambda kw, n: _ok("답"))
    msgs = [{"role": "user", "content": "질문"}]
    c.client.chat.completions.create(model="m", messages=msgs, max_tokens=1000, extra_body=c._extra)
    with effort.use("fast"):
        c.client.chat.completions.create(model="m", messages=msgs, max_tokens=1000, extra_body=c._extra)
    with effort.use("deep"):
        c.client.chat.completions.create(model="m", messages=msgs, max_tokens=1000, extra_body=c._extra)
        c.client.chat.completions.create(model="m", messages=msgs, max_tokens=1000)
    for kw in seen[:2]:
        assert kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False and kw["max_tokens"] == 1000
        assert "timeout" not in kw
    for kw in seen[2:]:
        assert kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
        assert kw["max_tokens"] > 1000 and kw["timeout"] >= 600
    assert c._extra == {"chat_template_kwargs": {"enable_thinking": False}}       # 연결의 기본 인자는 건드리지 않는다


def test_thinking_allowance_stays_inside_context(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path, lambda kw, n: _ok("답"))
    monkeypatch.setattr(llm_budget, "context_len", lambda cl: 4000)
    with effort.use("deep"):
        c.client.chat.completions.create(model="m", messages=[{"role": "user", "content": "질문"}], max_tokens=3000)
    assert 3000 < seen[0]["max_tokens"] <= 4000


def test_answer_options_add_thinking_room(monkeypatch):
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK_MAX_TOKENS", raising=False)
    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    assert responder.answer_gen_options(NS(_extra=extra)) == (3072, {"extra_body": extra})
    with effort.use("deep"):
        limit, gen = responder.answer_gen_options(NS(_extra=extra))
    assert limit >= 3072 + 8192 and gen["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


def test_self_check_follows_effort(monkeypatch):
    calls = []
    d = delegate.Delegator.__new__(delegate.Delegator)
    d.quality = True
    d._check = lambda *a, **k: calls.append(1)
    monkeypatch.setattr(delegate.llm_budget, "create_fitted",
                        lambda *a, **k: NS(choices=[NS(message=NS(content="[본문]\n본문 글"))]))
    monkeypatch.setattr(d, "limit", lambda c=None: 32768, raising=False)
    job = {"brief": {}, "need": [], "ev": "", "length": "", "redo": "", "n": 0, "guide": "", "section": "절:하나", "rec": {}}
    monkeypatch.setattr(delegate, "render_brief", lambda b, limit: "지시")
    for level, want in ((None, 1), ("fast", 1), ("deep", 2)):
        if level:
            with effort.use(level):
                d._write(dict(job, rec={}), None)
        else:
            d._write(dict(job, rec={}), None)
        assert len(calls) == want, level


def test_agent_steps_follow_effort(monkeypatch):
    n = {"calls": 0}

    def fake_create(client, messages, max_tokens, cuts=None, **kw):
        n["calls"] += 1
        return NS(choices=[NS(message=NS(content='{"thought": "찾기", "action": "look", "args": {"q": "' + str(n["calls"]) + '"}}'))])
    monkeypatch.setattr(llm_budget, "create_fitted", fake_create)
    tools = [doc_agent.Tool("look", "찾기", lambda q="": f"결과 {q}")]
    for level, steps in (("fast", 4), ("deep", 6)):
        n["calls"] = 0
        with effort.use(level):
            doc_agent.run(NS(model="m", _extra={}), "요청", lambda: "상태", tools, max_steps=4)
        assert n["calls"] == steps, level
