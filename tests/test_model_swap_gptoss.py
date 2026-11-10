"""다른 계열 모델(gpt-oss 등) 연결 — harmony 표시·생각 칸·도구 호출 꼴·생각만 하다 빈 답·빈 끝맺음에도 에이전트가 끝까지 간다(11-10).

사용자 보고: 시험으로 gpt-oss-120b 를 연결하자 「처리를 마쳤지만 남길 답을 만들지 못했습니다」 로 끝났다(finish 는 했는데 글이 비었다)."""
import json
from types import SimpleNamespace as NS

from zzaimy.app import doc_agent, effort
from zzaimy.generate import model_json

H_FINAL = ('<|channel|>analysis<|message|>Need to search first {"x": 1}<|end|><|start|>assistant'
           '<|channel|>final<|message|>{"action": "finish", "args": {"message": "끝냈습니다"}}<|return|>')
H_CALL = ('<|channel|>analysis<|message|>Search rules.<|end|><|start|>assistant'
          '<|channel|>commentary to=functions.search <|constrain|>json<|message|>{"query": "정년"}<|call|>')


def test_harmony_parts_split_thought_final_and_tool_call():
    thought, final, call = model_json.harmony_parts(H_FINAL)
    assert "Need to search" in thought and json.loads(final)["args"]["message"] == "끝냈습니다" and not call
    _t, final, call = model_json.harmony_parts(H_CALL)
    assert not final and json.loads(call) == {"action": "search", "args": {"query": "정년"}}
    assert model_json.loads(H_FINAL, key="action")["action"] == "finish"        # 생각 채널의 괄호는 답이 아니다
    assert model_json.loads(H_CALL, key="action") == {"action": "search", "args": {"query": "정년"}}
    assert model_json.strip_harmony("<|channel|>analysis<|message|>생각만") == ""    # 생각만 있으면 답이 없다
    assert model_json.strip_harmony("그냥 답") == "그냥 답"


# ---------------- 클라이언트 감싸개 ----------------

def _client(monkeypatch, tmp_path, behaviour, conn_opts=None):
    from zzaimy.generate import client as cl
    from zzaimy.generate import llm_connections

    seen = []

    class _Comp:
        def create(self, **kw):
            seen.append(kw)
            return behaviour(kw, len(seen))

    class _Fake:
        base_url = "http://other.example:8000/v1/"

        def __init__(self, **kw):
            self.chat = NS(completions=_Comp())

        def with_options(self, **kw):
            return _Fake()

    saved = (llm_connections._path, llm_connections._cache, llm_connections._mtime)
    llm_connections.configure(tmp_path / "llm_connections.json")
    conn = llm_connections.add("시험 gpt-oss", "vllm", "http://other.example:8000/v1", "m", "")
    llm_connections.activate(conn["id"])
    if conn_opts is not None:
        llm_connections.set_options(conn["id"], **conn_opts)
    monkeypatch.setattr(cl, "OpenAI", _Fake)
    monkeypatch.setattr(cl, "is_ollama", lambda url: False)
    try:
        return cl.VllmClient(model="m"), seen
    finally:
        llm_connections._path, llm_connections._cache, llm_connections._mtime = saved


def _r(content="", reasoning=None, reasoning_content=None, tool_calls=None, finish="stop"):
    msg = NS(content=content, tool_calls=tool_calls)
    if reasoning is not None:
        msg.reasoning = reasoning
    msg.reasoning_content = reasoning_content
    return NS(choices=[NS(message=msg, finish_reason=finish)], usage=None)


def _ask(c, **kw):
    return c.client.chat.completions.create(model="m", messages=[{"role": "user", "content": "질문"}], **kw).choices[0].message


def test_reply_shapes_become_plain_content(monkeypatch, tmp_path):
    fn = NS(name="search", arguments='{"query": "정년"}')
    replies = [_r(H_FINAL),                                                     # 해석기 없는 서버 — harmony 가 본문에
               _r("", reasoning=H_FINAL),                                       # 생각 칸(reasoning)에 final 까지 들어간 서버
               _r("", tool_calls=[NS(function=fn)]),                            # 도구 호출 꼴
               _r("답입니다", reasoning="생각 글")]                              # 생각 칸 이름이 reasoning
    c, _ = _client(monkeypatch, tmp_path, lambda kw, n: replies[n - 1])
    m1 = _ask(c)
    assert json.loads(m1.content)["action"] == "finish" and "Need to search" in m1.reasoning_content
    assert json.loads(_ask(c).content)["args"]["message"] == "끝냈습니다"
    assert json.loads(_ask(c).content) == {"action": "search", "args": {"query": "정년"}}
    m4 = _ask(c)
    assert m4.content == "답입니다" and m4.reasoning_content == "생각 글"         # 생각 글을 본문으로 올리지 않는다


def test_thinking_only_reply_is_retried_with_thinking_off(monkeypatch, tmp_path):
    """한도를 넓혀도 생각만 했다 — 생각을 끈 채(연결 설정이 없으면 reasoning_effort 낮게) 한 번 더 받는다."""
    def server(kw, n):
        if (kw.get("extra_body") or {}).get("reasoning_effort") == "low":
            return _r("정년은 65세가 되는 학기 말일입니다.")
        return _r("", reasoning="Let me think a lot...", finish="length")
    c, seen = _client(monkeypatch, tmp_path, server, conn_opts={"no_think": {}})   # 외부 서버처럼 끄는 인자를 정하지 않은 연결
    m = _ask(c, max_tokens=200)
    assert m.content.startswith("정년은") and len(seen) == 3
    assert seen[1]["max_tokens"] > seen[0]["max_tokens"]                        # 먼저 한도를 넓히고
    assert seen[2]["extra_body"]["reasoning_effort"] == "low"                     # 그래도 비면 생각을 끈다


def test_thinking_off_retry_rejected_returns_empty_not_error(monkeypatch, tmp_path):
    class _Rej(Exception):
        status_code = 400

    def server(kw, n):
        if (kw.get("extra_body") or {}).get("reasoning_effort"):
            raise _Rej("unknown field reasoning_effort")
        return _r("", reasoning="thinking", finish="length")
    c, seen = _client(monkeypatch, tmp_path, server, conn_opts={"no_think": {}})
    assert _ask(c, max_tokens=200).content == ""                                  # 호출부가 빈 답을 다룬다(예외로 끊기지 않게)
    assert len(seen) == 3


def test_explicit_thinking_call_is_not_turned_off(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path, lambda kw, n: _r("", reasoning="t", finish="length"))
    _ask(c, max_tokens=200, extra_body={"chat_template_kwargs": {"enable_thinking": True}})
    assert len(seen) == 2                                                          # 넓히기만, 생각 끄기 재청은 없다


# ---------------- 에이전트 루프 ----------------

def _agent_client(replies):
    it = iter(replies)

    class C:
        def create(self, **kw):
            return next(it)
    return NS(model="m", client=NS(chat=NS(completions=C())))


def _tools(answer=lambda a: "근거 답"):
    return [doc_agent.Tool("search", "찾기", lambda a: "결과 " + a.get("query", ""), gathers=True),
            doc_agent.Tool("answer_question", "답하기", answer, final=True),
            doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]


def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


def test_agent_runs_through_gpt_oss_reply_shapes():
    """harmony 도구 호출 → harmony final 의 finish — 감싸개를 거치지 않은 클라이언트에서도 걸음을 읽는다."""
    ep = doc_agent.run(_agent_client([_r(H_CALL), _r(H_FINAL)]), "정년 찾아 줘", lambda: "s", _tools())
    assert ep.steps[0]["action"] == "search" and ep.steps[0]["observation"] == "결과 정년"
    assert ep.finished and ep.final_message == "끝냈습니다"


def test_empty_finish_message_is_asked_again_once():
    ep = doc_agent.run(_agent_client([_r(_step("finish", message="")), _r(_step("finish", message="정년은 65세입니다."))]),
                       "정년?", lambda: "s", _tools())
    assert ep.finished and ep.final_message == "정년은 65세입니다."
    assert "message 가 비었다" in ep.steps[0]["observation"]


def test_twice_empty_finish_still_leaves_a_message():
    ep = doc_agent.run(_agent_client([_r(_step("search", query="정년")), _r(_step("finish", message="")), _r(_step("finish"))]),
                       "정년?", lambda: "s", _tools())
    assert ep.finished and ep.final_message.strip()                                # 빈 화면으로 끝내지 않는다
    assert "자료 찾기" in ep.final_message and doc_agent.failure_message(ep)


def test_empty_answer_tool_is_retried_instead_of_finishing():
    answers = iter(["", "정년은 65세가 되는 학기 말일까지입니다."])
    ep = doc_agent.run(_agent_client([_r(_step("search", query="정년")), _r(_step("answer_question", question="정년")),
                                      _r(_step("answer_question", question="정년"))]),
                       "정년?", lambda: "s", _tools(answer=lambda a: next(answers)))
    assert ep.finished and "학기 말일" in ep.final_message
    assert "빈 답" in ep.steps[1]["observation"]


# ---------------- 답 쓰기 조각 응답 ----------------

def test_stream_shows_only_harmony_final(monkeypatch):
    from zzaimy.app import llm_budget
    from zzaimy.app.responder import AgentResponder

    def ch(t, fin=None):
        return NS(usage=None, choices=[NS(delta=NS(content=t), finish_reason=fin)])
    chunks = [ch("<|channel|>analysis<|message|>Think about"), ch(" rules.<|end|><|start|>assistant<|channel|>final"),
              ch("<|message|>정년은 65세"), ch("입니다.<|return|>", "stop")]
    monkeypatch.setattr(llm_budget, "create_fitted", lambda *a, **k: iter(chunks))
    shown = []
    with effort.use("fast"):
        text, _ = AgentResponder._stream(object(), [], 1000, [], None, shown.append, gen={"extra_body": {}})
    assert text == "정년은 65세입니다."
    assert all("Think" not in s and "<|" not in s for s in shown)
