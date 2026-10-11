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
