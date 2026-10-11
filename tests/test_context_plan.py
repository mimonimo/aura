"""컨텍스트 구성 리즈닝 — Writer 가 찾을 것을 정하고 코드가 실행한다."""
from types import SimpleNamespace

from zzaimy.app import context_plan as cp


def _client(reply):
    class C:
        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
    return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))


def test_plan_execute_render_with_asks_searched_first():
    reply = ('{"thinking": "학과 현황과 실습 여건이 필요하다", "needs": [{"what": "참여 학과 교육과정", "source": "library", "query": "AI물류자동화과 교육과정"},'
             '{"what": "엉뚱한 곳", "source": "web", "query": "x"}], "ask": [{"name": "전기자동화과 전임교원 수", "why": "공시"}]}')
    p = cp.plan(_client(reply), doc_title="계획서", heading="1. 현황", guide="작성 지침", intent="쿠팡 협력", project_docs="(없음)", criteria_docs="(없음)")
    assert p["ok"] and [n["source"] for n in p["needs"]] == ["library"]          # 모르는 곳은 버린다
    calls = []

    def lib(q):
        calls.append(q)
        return [{"title": "지난 계획서", "doc_id": 1, "chunk_id": 9, "text": "전기자동화과 전임교원 12명"}] if "전임교원" in q else []
    found = cp.execute(p, search_project=lambda q: [], search_criteria=lambda q: [], search_library=lib)
    assert calls == ["AI물류자동화과 교육과정", "전기자동화과 전임교원 수"]                 # 물을 값도 먼저 찾아본다
    text = cp.render(p, found)
    assert "《지난 계획서》" in text and "전임교원 12명" in text and "찾지 못한 값" not in text


def test_plan_failure_is_empty():
    assert cp.plan(_client("모르겠다"), doc_title="", heading="", guide="", intent="", project_docs="", criteria_docs="")["needs"] == []
    assert cp.plan(None, doc_title="", heading="", guide="", intent="", project_docs="", criteria_docs="")["ok"] is False
