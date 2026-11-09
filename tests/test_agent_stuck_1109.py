"""11-09 대화 165 망가짐 재현 — 끝내기 관문 뒤 같은 finish 를 받고, 0곳 바꾸기 되풀이를 끊고, 사본보다 원본 작업본을 고른다."""
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


def test_same_finish_after_one_time_gate_is_accepted():
    gate = {"done": False}

    def fin(a):
        if not gate["done"]:
            gate["done"] = True
            raise doc_agent.Retry("끝내기 전에: 자리표시가 남아 있다")
        return a.get("message", "")
    tools = [doc_agent.Tool("search", "찾기", lambda a: "결과"), doc_agent.Tool("finish", "끝", fin, final=True)]
    msg = "「시험1」을 찾지 못했습니다"
    ep = doc_agent.run(_client([_step("finish", message=msg), _step("finish", message=msg)]), "지워 줘", lambda: "s", tools)
    assert ep.finished and ep.final_message == msg                       # 같은 인자의 두 번째 finish 를 반복 금지로 막지 않는다


def test_noop_replace_streak_is_cut_before_write_limit():
    ran = []
    tools = [doc_agent.Tool("hwp_replace", "바꾸기", lambda a: ran.append(a["find"]) or f"바꾸지 않았다 — 「{a['find']}」 을 찾지 못했다(0곳).",
                            writes=True),
             doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]
    replies = [_step("hwp_replace", find=f"test{i}", replace="") for i in range(1, 8)] + [_step("finish", message="찾지 못했습니다")] * 3
    ep = doc_agent.run(_client(replies), "지워 줘", lambda: "s", tools, max_writes=30)
    assert ran == ["test1", "test2", "test3"]                            # 세 번 0곳 뒤로는 더 돌리지 않는다
    assert "더 하지 않는다" in ep.steps[3]["observation"]
    assert ep.writes <= 3


def test_switch_prefers_original_over_copy_and_filled(tmp_path, monkeypatch):
    from zzaimy.app import webhwp_agent as wa
    hw = wa.HwpTools.__new__(wa.HwpTools)
    docs = [{"id": 2, "name": "2027학년도 AI 교육 사업계획서 (2)"}, {"id": 1, "name": "2027학년도 AI 교육 사업계획서"}]
    monkeypatch.setattr(hw, "_filled", lambda fid: {1: 30, 2: 0}[fid], raising=False)
    assert hw._best_of(docs, "사업계획서")["id"] == 1                    # 사본 꼬리 없는 원본, 더 채운 쪽
    assert hw._best_of(docs, "2027학년도 AI 교육 사업계획서 (2)")["id"] == 2   # 이름을 그대로 짚으면 그것
