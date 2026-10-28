from types import SimpleNamespace as NS

from zzaimy.app import effort
from zzaimy.app.responder import AgentResponder
from zzaimy.generate.client import _split_think


def _chunk(text=None, finish=None):
    return NS(usage=None, choices=[NS(delta=NS(content=text), finish_reason=finish)])


def test_non_stream_keeps_only_text_after_think():
    msg = NS(content="생각 글 영어 reasoning ... </think>\n\n[답] 회의비는 자체 기준 준용", reasoning_content=None)
    resp = NS(choices=[NS(message=msg)])
    _split_think(resp)
    assert msg.content == "[답] 회의비는 자체 기준 준용"
    assert "reasoning" in msg.reasoning_content
    plain = NS(content="그냥 답", reasoning_content=None)
    _split_think(NS(choices=[NS(message=plain)]))
    assert plain.content == "그냥 답"


def test_stream_hides_thinking_until_close_tag(monkeypatch):
    from zzaimy.app import llm_budget
    chunks = [_chunk("Need concise. "), _chunk("Let's produce final.\n</th"), _chunk("ink>\n\n[답] 본문"), _chunk(" 끝", "stop")]
    monkeypatch.setattr(llm_budget, "create_fitted", lambda *a, **k: iter(chunks))
    shown = []
    with effort.use("deep"):
        text, finish = AgentResponder._stream(object(), [], 1000, [], None, shown.append, gen={"extra_body": {}})
    assert text == "[답] 본문 끝" and finish == "stop"
    assert all("Need" not in s and "think" not in s for s in shown)


def test_stream_without_thinking_unchanged(monkeypatch):
    from zzaimy.app import llm_budget
    monkeypatch.setattr(llm_budget, "create_fitted", lambda *a, **k: iter([_chunk("답 "), _chunk("입니다", "stop")]))
    with effort.use("fast"):
        text, _ = AgentResponder._stream(object(), [], 1000, [], None, lambda s: None, gen={"extra_body": {}})
    assert text == "답 입니다"


def test_cut_off_thinking_is_not_answer():
    from types import SimpleNamespace
    from zzaimy.generate.client import _split_think, _thinking_on
    msg = SimpleNamespace(content="But rule: need source... 가) 초안 글", reasoning_content=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="length")])
    _split_think(resp, _thinking_on({"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}))
    assert msg.content == "" and "초안" in msg.reasoning_content


def test_cut_off_without_thinking_kept():
    from types import SimpleNamespace
    from zzaimy.generate.client import _split_think
    msg = SimpleNamespace(content="가) 길게 쓴 본문", reasoning_content=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="length")])
    _split_think(resp, False)
    assert msg.content == "가) 길게 쓴 본문"
