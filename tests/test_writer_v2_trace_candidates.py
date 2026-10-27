"""Only valid, traceable test calls become unapproved Writer v2 candidates."""
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

from zzaimy.dataset import privacy


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "214_writer_v2_trace_candidates.py"
spec = importlib.util.spec_from_file_location("writer_v2_trace_candidates", SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def _row(**updates):
    row = {"caller": "zzaimy.app.doc_agent.plan", "model": "zzaimy-writer",
           "connection_id": "internal-writer", "kind": "vllm", "external": False,
           "messages": [{"role": "system", "content": "JSON 단계만\n도구:\n- write_section: 절 작성\n\n생각"},
                        {"role": "user", "content": "초안을 작성해 줘"}],
           "tools": [{"type": "function", "function": {"name": "write_section"}}],
           "response_format": {"type": "json_object"}, "max_tokens": 256,
           "attempt": 1, "reasoning_present": False,
           "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
           "answer": '{"action":"write_section","args":{}}', "finish": "stop"}
    row.update(updates)
    return row


def test_preserves_real_request_and_answer_as_unapproved_candidate(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    pair, reason = collector._candidate(_row())
    assert reason == "accepted_for_review"
    assert pair["status"] == "review_pending"
    assert pair["execution_verified"] is False
    assert "messages" not in pair
    assert pair["trace_messages"][-1] == {"role": "assistant", "content": '{"action":"write_section","args":{}}'}
    assert pair["request"]["tools"][0]["function"]["name"] == "write_section"
    assert pair["request"]["response_format"] == {"type": "json_object"}
    assert pair["request"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_rejects_empty_truncated_and_unrelated_calls(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    assert collector._candidate(_row(answer=""))[1] == "empty_answer"
    assert collector._candidate(_row(finish="length"))[1] == "unfinished_answer"
    assert collector._candidate(_row(caller="zzaimy.eval.scorer.score"))[1] == "unrelated_caller"
    assert collector._candidate(_row(caller="zzaimy.app.pipeline._review"))[1] == "unrelated_caller"
    assert collector._candidate(_row(model="other-model"))[1] == "wrong_model"
    assert collector._candidate(_row(connection_id=""))[1] == "unverified_connection"
    assert collector._candidate(_row(extra_body={"chat_template_kwargs": {"enable_thinking": True}}))[1] == "thinking_enabled"
    assert collector._candidate(_row(extra_body=None))[1] == "unrecorded_thinking_mode"
    assert collector._candidate(_row(extra_body={}))[1] == "thinking_enabled"
    assert collector._candidate(_row(attempt=2))[1] == "retry_response"
    assert collector._candidate(_row(reasoning_present=True))[1] == "reasoning_trace"
    assert collector._candidate(_row(reasoning_present=None))[1] == "reasoning_trace"
    assert collector._candidate(_row(answer="<think>분석</think>답"))[1] == "reasoning_trace"
    assert collector._candidate(_row(caller="zzaimy.app.responder.answer"))[1] == "incomplete_reply_capture"
    assert collector._candidate(_row(caller="zzaimy.app.delegate._check"))[1] == "auxiliary_delegate_call"
    assert collector._candidate(_row(answer="근거 설명: 자료 확인\n답변: 작성하겠습니다"))[1] == "legacy_answer_frame"


def test_rejects_unmatched_delegate_tool_call(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    call = {"id": "call-1", "type": "function", "function": {
        "name": "write_section", "arguments": '{"section":"개요"}'}}
    assert collector._candidate(_row(caller="zzaimy.app.delegate.run", answer="", tool_calls=[call], finish="tool_calls"))[1] == "unsupported_caller_shape"


def test_collect_deduplicates_and_keeps_trace(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    path = tmp_path / "llm-2026-10-25.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in (
        _row(), _row(), _row(answer=""))) + "\n", encoding="utf-8")
    candidates, held, counts = collector.collect(tmp_path)
    assert len(candidates) == 1
    assert held == []
    assert candidates[0]["trace"] == {"file": path.name, "line": 1}
    assert counts["duplicate"] == 1
    assert counts["empty_answer"] == 1
    assert counts["caller:zzaimy.app.doc_agent.plan"] == 1


def test_privacy_changes_hold_candidate(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: {**value, "caller": "masked"})
    assert collector._candidate(_row())[1] == "privacy_review"


def test_clean_synthetic_call_passes_real_privacy_check():
    assert collector._candidate(_row())[1] == "accepted_for_review"


def test_collect_retains_privacy_hold_for_review(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: {**value, "caller": "masked"})
    (tmp_path / "llm-2026-10-25.jsonl").write_text(json.dumps(_row(), ensure_ascii=False) + "\n")
    candidates, held, counts = collector.collect(tmp_path)
    assert candidates == [] and counts["privacy_review"] == 1
    assert held[0]["reason"] == "privacy_review"
    assert held[0]["record"]["answer"] == '{"action":"write_section","args":{}}'


def test_rejects_agent_steps_the_platform_cannot_use(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    assert collector._candidate(_row(answer="설명을 먼저 드리겠습니다."))[1] == "agent_step_unparsed"
    assert collector._candidate(_row(answer='{"action":"unknown","args":{}}'))[1] == "agent_step_unknown_tool"
    assert collector._candidate(_row(answer='앞말 {"action":"write_section","args":{}}'))[1] == "agent_step_unparsed"
    assert collector._candidate(_row(answer='{"action":"write_section","args":[]}'))[1] == "agent_step_unparsed"
    assert collector._candidate(_row(messages=[
        _row()["messages"][0],
        {"role": "user", "content": "도구 이름이 없거나 JSON 이 아니다. 위 도구 가운데 하나를 고른 JSON 하나만 다시 답하라."},
    ]))[1] == "agent_correction_turn"
    assert collector._candidate(_row(messages=[
        {"role": "system", "content": "도구 목록 없음"},
        {"role": "user", "content": "초안을 작성해 줘"},
    ]))[1] == "agent_tool_catalog_missing"
    assert collector._candidate(_row())[1] == "accepted_for_review"


def test_caller_specific_output_contracts(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    memo = "지금 목표:\n- 계획서 작성\n확정된 것:\n- 없음\n진행 상황:\n- 시작\n남은 일·물어볼 것:\n- 자료 확인"
    assert collector._candidate(_row(caller="zzaimy.app.chat_memory.compact", answer=memo))[1] == "accepted_for_review"
    assert collector._candidate(_row(caller="zzaimy.app.chat_memory.compact", answer="계획서 작성"))[1] == "memo_format"
    section = "[본문]\n1) 사업 목적을 근거에 맞춰 작성함\n[확인 필요]\n- 없음"
    assert collector._candidate(_row(caller="zzaimy.app.delegate._write", answer=section))[1] == "accepted_for_review"
    assert collector._candidate(_row(caller="zzaimy.app.delegate.run", answer="절을 쓰겠습니다"))[1] == "section_format"
    assert collector._candidate(_row(caller="zzaimy.app.delegate._plan", answer=section))[1] == "unsupported_caller_shape"


def test_cli_requires_private_output_and_creates_private_files(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    records = tmp_path / "records"
    records.mkdir()
    records.chmod(0o700)
    record = records / "llm-1.jsonl"
    record.write_text(json.dumps(_row(), ensure_ascii=False) + "\n")
    record.chmod(0o600)
    output = tmp_path / "output"
    output.mkdir()
    output.chmod(0o700)
    argv = [str(SCRIPT), "--record-dir", str(records), "--out", str(output / "review.jsonl"),
            "--held-out", str(output / "held.jsonl")]
    monkeypatch.setattr(sys, "argv", argv)
    assert collector.main() == 0
    for name in ("review.jsonl", "held.jsonl"):
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o600

    output.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [*argv[:-2], "--held-out", str(output / "next.jsonl")])
    with pytest.raises(SystemExit):
        collector.main()
    assert not (output / "next.jsonl").exists()

    output.chmod(0o700)
    record.chmod(0o644)
    with pytest.raises(SystemExit):
        collector.main()
