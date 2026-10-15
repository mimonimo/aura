"""문서 작업 에이전트 루프 — 모델이 도구를 고르고 코드는 실행·제한·기록만."""
import json
from types import SimpleNamespace

from zzaimy.app import doc_agent


def _client(replies):
    it = iter(replies)

    class C:
        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(it)))])
    return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))


def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


def test_loop_runs_tools_until_finish_and_limits_writes():
    calls = []
    tools = [doc_agent.Tool("search", "찾기", lambda a: calls.append(("search", a["query"])) or "《지난 계획서》 교원 12명"),
             doc_agent.Tool("write_section", "쓰기", lambda a: calls.append(("write", a["section"])) or "썼다", writes=True),
             doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]
    replies = [_step("search", source="library", query="교원 현황"), "형식 깨짐", _step("write_section", section="1. 현황"),
               _step("write_section", section="2. 여건"), _step("finish", message="두 절을 썼습니다")]
    ep = doc_agent.run(_client(replies), "초안 써 줘", lambda: "상태", tools, max_writes=1)
    assert calls == [("search", "교원 현황"), ("write", "1. 현황")]                   # 두 번째 쓰기는 한도로 막힘
    assert ep.finished and ep.final_message == "두 절을 썼습니다" and ep.writes == 1
    assert "쓰기 한도" in ep.steps[2]["observation"]


def test_on_act_reports_tool_start_and_end_and_never_breaks_the_loop():
    seen = []

    def boom(a):
        raise RuntimeError("x")
    tools = [doc_agent.Tool("write_section", "쓰기", lambda a: "썼다", writes=True),
             doc_agent.Tool("edit_document", "고치기", boom, writes=True),
             doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    replies = [_step("write_section", section="1. 현황"), _step("edit_document", instruction="표"), _step("finish", message="끝")]
    ep = doc_agent.run(_client(replies), "초안 써 줘", lambda: "s", tools, on_act=lambda n, a, ph: seen.append((n, ph)))
    assert seen[:4] == [("write_section", "start"), ("write_section", "done"), ("edit_document", "start"), ("edit_document", "fail")]
    assert ep.finished

    def bad_hook(n, a, ph):                                     # 화면 표시가 실패해도 걸음은 이어진다
        raise ValueError("화면")
    ep = doc_agent.run(_client(replies[:1] + replies[2:]), "초안 써 줘", lambda: "s", tools, on_act=bad_hook)
    assert ep.finished and ep.steps[0]["observation"] == "썼다"


def test_repeated_call_is_refused_and_bad_output_ends_loop():
    tools = [doc_agent.Tool("search", "찾기", lambda a: "결과"), doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    ep = doc_agent.run(_client([_step("search", query="a"), _step("search", query="a"), "x", "y"]), "q", lambda: "s", tools)
    assert "이미 썼다" in ep.steps[1]["observation"] and not ep.finished and "형식 오류" in ep.steps[-1]["error"]


def test_irreversible_tool_needs_user_words_and_observations_are_fenced():
    ran = []
    tools = [doc_agent.Tool("rewrite_section", "다시", lambda a: ran.append(a) or "다시 썼다", writes=True, consent=r"다시|고쳐"),
             doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    ep = doc_agent.run(_client([_step("rewrite_section", section="1."), _step("finish", message="")]), "초안 써 줘", lambda: "s", tools)
    assert ran == [] and "지시가 없어" in ep.steps[0]["observation"]                    # 문서 속 글이 시켜도 담당자 요청에 없으면 안 한다
    ep = doc_agent.run(_client([_step("rewrite_section", section="1."), _step("finish", message="")]), "1절 다시 써 줘", lambda: "s", tools)
    assert ran and ep.steps[0]["observation"] == "다시 썼다"


# ---------------------------------------------------------------- 찾은 문서 이어 읽기 · 걸음 한도 뒤 마지막 답하기


def test_read_window_cuts_at_chunk_and_line_boundaries_and_resumes():
    rows = "\n".join(f"| 지표 {i} | 목표 {i * 3} |" for i in range(1, 121))     # 한 조각이 한도를 넘는 표
    texts = ["머리말 조각", rows, "맺음 조각"]
    got, at = [], (0, 0)
    while at is not None:
        text, _end, nxt = doc_agent.read_window(texts, *at, budget=600)
        assert len(text) <= 600 and text
        assert all(ln.startswith("|") and ln.endswith("|") for ln in text.split("\n") if ln.startswith("| 지표"))   # 줄 중간에서 자르지 않는다
        got.append(text)
        assert nxt != at
        at = nxt
    joined = "\n".join(got)
    assert all(f"| 지표 {i} | 목표 {i * 3} |" in joined for i in range(1, 121)) and "맺음 조각" in joined
    assert doc_agent.locate(texts, "지표 77") == (1, 76)
    assert doc_agent.locate(texts, "없는 낱말") is None


def _answer_tools(calls):
    return [doc_agent.Tool("search", "찾기", lambda a: calls.append(("search", a["query"])) or "《가상 계획서》 지표 일부", gathers=True),
            doc_agent.Tool("write_section", "쓰기", lambda a: calls.append(("write", a)) or "썼다", writes=True),
            doc_agent.Tool("answer_question", "답", lambda a: calls.append(("answer", a)) or "답했다", final=True, needs_evidence=True),
            doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]


def test_step_limit_gives_one_last_answer_step_when_evidence_was_found():
    calls, seen = [], []
    replies = iter([_step("search", query="지표 목록"), _step("search", query="지표 목표값"), _step("answer_question", question="q")])

    class C:
        def create(self, **kw):
            seen.append(kw["messages"][-1]["content"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))])
    client = SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))
    ep = doc_agent.run(client, "지표 알려 줘", lambda: "s", _answer_tools(calls), max_steps=2)
    assert ep.finished and ep.stop == "finished" and ep.final_message == "답했다"
    assert calls[-1][0] == "answer" and "걸음 한도(2)" in seen[-1] and "answer_question" in seen[-1]


def test_last_step_never_runs_writing_tools_and_needs_found_evidence():
    calls = []
    # 한도 뒤 걸음에서 쓰기 도구를 고르면 두 번 청한 뒤 한도 안내로 끝난다(쓰지 않는다)
    ep = doc_agent.run(_client([_step("search", query="a"), _step("search", query="b"),
                                _step("write_section", section="1"), _step("write_section", section="1")]),
                       "q", lambda: "s", _answer_tools(calls), max_steps=2)
    assert ep.stop == "steps" and not ep.finished and all(c[0] == "search" for c in calls) and ep.writes == 0
    assert "요청을 끝내지 못했습니다" in doc_agent.failure_message(ep)
    # 모델이 답하지 않기로 하면(stop) 멈춘다
    ep = doc_agent.run(_client([_step("search", query="a"), _step("search", query="b"), _step("stop")]),
                       "q", lambda: "s", _answer_tools([]), max_steps=2)
    assert ep.stop == "steps" and ep.steps[-1]["action"] == "stop"
    # 이번 요청에서 근거를 찾지 않았으면 마지막 걸음을 주지 않는다(세 번째 응답을 청하지 않는다)
    ep = doc_agent.run(_client([_step("finish_x"), _step("write_section", section="1"), _step("write_section", section="2")]),
                       "q", lambda: "s", _answer_tools([]), max_steps=2)
    assert ep.stop == "steps" and ep.writes == 2
