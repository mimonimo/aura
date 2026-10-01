"""Keep fixed instructions intact and never submit an over-budget edit request."""
import pytest
from types import SimpleNamespace

from zzaimy.app import gdocs_agent as agent


def test_large_overflow_removes_materials_then_body(monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 5000)
    materials = "자료: 원문 근거 120명\n" * 700
    body = "본문: 기존 문장\n" * 700
    fixed = "[출처] 문서 42 / 절 3\n[담당자 지시] 사업을 바꾸지 마세요."
    prompt = materials + body + fixed
    fitted, tokens = agent._fit_context(prompt, 2048, materials, body)
    assert agent._est_tokens(fitted) + tokens <= 5000 - 256
    assert fitted.endswith(fixed)
    assert "생략" in fitted or "일부만" in fitted


def test_unshrinkable_prompt_is_rejected(monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 5000)
    with pytest.raises(ValueError, match="문맥"):
        agent._fit_context("담당자 지시 " * 4000, 2048, "", "")


def test_short_prompt_unchanged():
    assert agent._fit_context("짧은 요청", 2048, "", "") == ("짧은 요청", 2048)


def test_instruction_containing_same_material_is_not_rewritten(monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 5000)
    materials = "동일한 근거 자료 " * 1000
    with pytest.raises(ValueError, match="문맥"):
        agent._fit_context(materials + "\n[담당자 지시]\n" + materials, 2048, materials, "")


def planner():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"reply":"확인 필요", "ops":[], "asks":[]}'
        ))])

    return SimpleNamespace(model="fake", client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )), calls


def test_plan_normalizes_materials_before_budgeting(monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 7000)
    fake, calls = planner()
    command = "2025년 ALPHA 사업 문서 42의 3절만 검토하세요."
    agent.plan(fake, command, {"title": "ALPHA 사업", "sections": [], "text": "대상 본문"},
               materials="   \n" + "근거 문서 42 / 2025년 / 참여 목표 120명\n" * 2000 + "   ")
    assert len(calls) == 1
    request = calls[0]
    prompt = request["messages"][0]["content"]
    assert agent._est_tokens(prompt) + request["max_tokens"] <= 7000 - 256
    assert command in prompt and "[재료 일부 생략" in prompt


def test_plan_does_not_call_model_when_fixed_context_cannot_fit(monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 5000)
    fake, calls = planner()
    with pytest.raises(ValueError, match="문맥"):
        agent.plan(fake, "정정된 지시 " * 4000, {"title": "대상", "sections": [], "text": ""})
    assert calls == []
