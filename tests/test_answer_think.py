"""답 쓰기의 생각 모드 손잡이 — 기본(운영)은 지금처럼 끄고, ZZAIMY_ANSWER_THINK=1 일 때만 켠다. 생각 글은 답에 섞지 않는다."""
from types import SimpleNamespace as NS

import pytest

from zzaimy.app import llm_budget, responder

_VLLM_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}


def _fake(extra, calls, *, stream_parts=None):
    def create(**kw):
        calls.append(kw)
        if kw.get("stream"):
            return iter(stream_parts or [])
        msg = NS(content="계획 목표는 120명입니다.", reasoning_content="먼저 근거를 살펴보면 ... 120명")
        return NS(choices=[NS(message=msg, finish_reason="stop")], usage=NS(completion_tokens=321, prompt_tokens=10))
    return NS(model="m", connection_id=None, _extra=extra, client=NS(chat=NS(completions=NS(create=create))))


def _answer(monkeypatch, fake, **kw):
    from zzaimy.generate import client
    chunks = [{"doc_id": 2, "reg_title": "근거", "content": "참여 목표 120명"}]
    db = NS(chunks_for_docs=lambda ids: chunks, all_settings=lambda: {})
    monkeypatch.setattr(client, "VllmClient", lambda **k: fake)
    monkeypatch.setattr(responder, "rank_criteria_chunks", lambda *a: chunks)
    llm_budget.reset_cache()
    ar = responder.AgentResponder()
    return ar, ar.answer(db, "참여 목표는?", criteria_ids=[2], **kw)


def test_default_keeps_thinking_off_and_old_limit(monkeypatch):
    """운영 기본 — 환경변수가 없으면 지금과 같은 호출(생각 끔·3,072 토큰·시간 제한 인자 없음)."""
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    monkeypatch.delenv("ZZAIMY_ANSWER_MAX_TOKENS", raising=False)
    calls = []
    ar, out = _answer(monkeypatch, _fake(dict(_VLLM_EXTRA), calls))
    assert calls[0]["extra_body"] == _VLLM_EXTRA
    assert calls[0]["max_tokens"] <= 3072 and "timeout" not in calls[0]
    assert ar.last_timing["think"] is False and ar.last_timing["completion_tokens"] == 321
    assert out == "계획 목표는 120명입니다."


@pytest.mark.parametrize("val", ["0", "", "no"])
def test_off_values_do_not_turn_thinking_on(monkeypatch, val):
    monkeypatch.setenv("ZZAIMY_ANSWER_THINK", val)
    assert responder.answer_gen_options(NS(_extra=dict(_VLLM_EXTRA))) == (3072, {"extra_body": _VLLM_EXTRA})


def test_knob_turns_thinking_on_and_hides_reasoning(monkeypatch):
    monkeypatch.setenv("ZZAIMY_ANSWER_THINK", "1")
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK_MAX_TOKENS", raising=False)
    calls = []
    extra = dict(_VLLM_EXTRA)
    ar, out = _answer(monkeypatch, _fake(extra, calls))
    assert calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert calls[0]["max_tokens"] > 3072 and calls[0]["timeout"] >= 600
    assert extra == _VLLM_EXTRA                                     # 연결의 기본 인자는 건드리지 않는다
    assert "살펴보면" not in out and ar.last_timing["think"] is True


def test_knob_is_ignored_for_servers_without_vllm_extension(monkeypatch):
    """외부 API(확장 인자를 보내지 않는 연결)에는 생각 켜기 인자를 보내지 않는다."""
    monkeypatch.setenv("ZZAIMY_ANSWER_THINK", "1")
    assert responder.answer_gen_options(NS(_extra={})) == (3072, {"extra_body": {}})


def _chunk(text=None, reasoning=None, finish=None, usage=None):
    delta = NS(content=text, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=finish)] if (text or reasoning or finish) else [], usage=usage)


@pytest.mark.parametrize("think", [False, True])
def test_stream_path_follows_knob_and_skips_reasoning(monkeypatch, think):
    if think:
        monkeypatch.setenv("ZZAIMY_ANSWER_THINK", "1")
    else:
        monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    parts = [_chunk(reasoning="생각 중 ..."), _chunk("답은 "), _chunk("120명입니다."), _chunk(finish="stop"),
             _chunk(usage=NS(completion_tokens=77))]
    calls, partial = [], []
    ar, out = _answer(monkeypatch, _fake(dict(_VLLM_EXTRA), calls, stream_parts=parts), on_partial=partial.append)
    assert calls[0]["stream"] is True and len(calls) == 1
    assert calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is think
    assert out == "답은 120명입니다." and all("생각" not in p for p in partial)
    assert ar.last_timing["completion_tokens"] == 77
