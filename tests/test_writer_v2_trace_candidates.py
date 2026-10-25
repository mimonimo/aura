"""Only valid, traceable test calls become unapproved Writer v2 candidates."""
import importlib.util
import json
from pathlib import Path

from zzaimy.dataset import privacy


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "214_writer_v2_trace_candidates.py"
spec = importlib.util.spec_from_file_location("writer_v2_trace_candidates", SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def _row(**updates):
    row = {"caller": "zzaimy.app.doc_agent.plan", "model": "zzaimy-writer",
           "connection_id": "internal-writer", "kind": "vllm", "external": False,
           "messages": [{"role": "system", "content": "JSON 단계만"},
                        {"role": "user", "content": "초안을 작성해 줘"}],
           "tools": [{"type": "function", "function": {"name": "write_section"}}],
           "response_format": {"type": "json_object"}, "max_tokens": 256,
           "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
           "answer": '{"step":"write"}', "finish": "stop"}
    row.update(updates)
    return row


def test_preserves_real_request_and_answer_as_unapproved_candidate(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    pair, reason = collector._candidate(_row())
    assert reason == "accepted_for_review"
    assert pair["status"] == "review_pending"
    assert pair["messages"][-1] == {"role": "assistant", "content": '{"step":"write"}'}
    assert pair["request"]["tools"][0]["function"]["name"] == "write_section"
    assert pair["request"]["response_format"] == {"type": "json_object"}
    assert pair["request"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_rejects_empty_truncated_and_unrelated_calls(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    assert collector._candidate(_row(answer=""))[1] == "empty_answer"
    assert collector._candidate(_row(finish="length"))[1] == "unfinished_answer"
    assert collector._candidate(_row(caller="zzaimy.eval.scorer.score"))[1] == "unrelated_caller"
    assert collector._candidate(_row(model="other-model"))[1] == "wrong_model"
    assert collector._candidate(_row(connection_id=""))[1] == "unverified_connection"
    assert collector._candidate(_row(extra_body={"chat_template_kwargs": {"enable_thinking": True}}))[1] == "thinking_enabled"
    assert collector._candidate(_row(extra_body=None))[1] == "unrecorded_thinking_mode"


def test_keeps_tool_call_completion(monkeypatch):
    monkeypatch.setattr(privacy, "protect_candidate", lambda value: value)
    call = {"id": "call-1", "type": "function", "function": {
        "name": "write_section", "arguments": '{"section":"개요"}'}}
    pair, reason = collector._candidate(_row(answer="", tool_calls=[call], finish="tool_calls"))
    assert reason == "accepted_for_review"
    assert pair["messages"][-1]["tool_calls"] == [call]


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
    assert held[0]["record"]["answer"] == '{"step":"write"}'
