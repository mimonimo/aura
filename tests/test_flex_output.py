"""출력 한도는 남은 문맥 몫 전부로(고정하지 않음), 넘치는 입력은 잘라 버리기 전에 줄여 이어 간다(11-17)."""
from types import SimpleNamespace

from zzaimy.app import llm_budget as lb
from zzaimy.generate import client as gc


class _Comp:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def _cli(reply="줄인 글", ctx=10000):
    comp = _Comp(reply)
    c = SimpleNamespace(model="m", _extra={}, client=SimpleNamespace(base_url="http://x", chat=SimpleNamespace(completions=comp)))
    lb._LEN_CACHE[(lb._base(c), "m")] = (ctx, 9e18, True)
    return c, comp


def test_output_limit_widens_to_remaining_context(monkeypatch):
    monkeypatch.setenv("ZZAIMY_FLEX_OUTPUT", "1")
    c, _ = _cli(ctx=10000)
    kw = gc._flex_output(c, {"messages": [{"role": "user", "content": "질문"}], "max_tokens": 120})
    assert kw["max_tokens"] > 9000 and kw["_asked_tokens"] == 120


def test_flex_off_keeps_caller_limit(monkeypatch):
    monkeypatch.setenv("ZZAIMY_FLEX_OUTPUT", "0")
    c, _ = _cli()
    assert gc._flex_output(c, {"messages": [], "max_tokens": 120})["max_tokens"] == 120


def test_overflowing_block_is_compressed_not_cut(monkeypatch):
    monkeypatch.setenv("ZZAIMY_COMPRESS", "1")
    c, comp = _cli(reply="요약: 금액 5억 원, 2027. 3. 1.")
    block = "근거 문단 " * 2000
    out = lb._compress(c, block, len(block) - 1000, "…")
    assert out.startswith("요약: 금액 5억 원") and comp.calls


def test_compress_falls_back_to_cut_when_summary_too_long(monkeypatch):
    monkeypatch.setenv("ZZAIMY_COMPRESS", "1")
    c, _ = _cli(reply="너무 긴 요약 " * 5000)
    block = "줄\n" * 5000
    out = lb._compress(c, block, len(block) - 1000, "…")
    assert len(out) <= 1100
