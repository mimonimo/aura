#!/usr/bin/env python3
"""Make review candidates from one isolated Writer scenario's LLM call log.

This does not approve or split training data. Keep the input and output on the
training machine; recorded prompts can contain complete source documents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path


def _candidate(row: dict) -> tuple[dict | None, str]:
    caller = str(row.get("caller") or "")
    if not caller.startswith(("zzaimy.app.doc_agent.", "zzaimy.app.chat_memory.",
                              "zzaimy.app.responder.", "zzaimy.app.delegate.")):
        return None, "unrelated_caller"
    # The main responder reply streams and is not recorded here. A recorded
    # responder call is therefore a fallback or a continuation, not a sample
    # of ordinary replies. Keep it out until stream capture is complete.
    if caller.startswith("zzaimy.app.responder."):
        return None, "incomplete_reply_capture"
    if caller in {"zzaimy.app.delegate._queries", "zzaimy.app.delegate._check"}:
        return None, "auxiliary_delegate_call"
    if row.get("model") != "zzaimy-writer":
        return None, "wrong_model"
    if (row.get("kind") != "vllm" or row.get("external") is not False
            or not isinstance(row.get("connection_id"), str) or not row["connection_id"]):
        return None, "unverified_connection"
    if row.get("attempt") != 1:
        return None, "retry_response"
    if row.get("reasoning_present") is not False:
        return None, "reasoning_trace"
    body = row.get("extra_body")
    if not isinstance(body, dict):
        return None, "unrecorded_thinking_mode"
    thinking = body.get("chat_template_kwargs") or {}
    if not isinstance(thinking, dict):
        return None, "unrecorded_thinking_mode"
    explicitly_off = (thinking.get("enable_thinking") is False
                      or body.get("reasoning_effort") in ("none", "off"))
    if (not explicitly_off or thinking.get("enable_thinking") is True
            or body.get("reasoning_effort") not in (None, "none", "off")):
        return None, "thinking_enabled"
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages or any(
        not isinstance(msg, dict) or msg.get("role") not in {"system", "user", "assistant", "tool"}
        or (not isinstance(msg.get("content"), str)
            and not (msg.get("role") == "assistant" and msg.get("content") is None
                     and msg.get("tool_calls"))) for msg in messages
    ):
        return None, "invalid_messages"
    answer = row.get("answer")
    tool_calls = row.get("tool_calls") or []
    if not isinstance(answer, str) or not isinstance(tool_calls, list):
        return None, "invalid_answer"
    if "<think" in answer.lower() or "</think>" in answer.lower():
        return None, "reasoning_trace"
    if not answer.strip() and not tool_calls:
        return None, "empty_answer"
    if row.get("finish") not in ({"stop", "tool_calls"} if tool_calls else {"stop"}):
        return None, "unfinished_answer"
    # Writer v1 learned these labels in every answer and then emitted them in
    # unrelated platform calls. Do not recycle that failure into v2.
    if re.search(r"(?m)^\s*(?:근거 설명|검색 계획|답변)\s*:", answer):
        return None, "legacy_answer_frame"
    if caller.startswith("zzaimy.app.doc_agent."):
        # The recorder sees the model reply before the agent parses or executes it.
        # Keep rejected JSON and correction turns out of even the review queue.
        if any(msg["role"] == "user" and any(marker in msg["content"] for marker in (
            "도구 이름이 없거나 JSON 이 아니다.",
            "마지막 걸음이다 — ",
        )) for msg in messages):
            return None, "agent_correction_turn"
        try:
            step = json.loads(answer.strip())
        except json.JSONDecodeError:
            step = None
        if (tool_calls or not isinstance(step, dict) or not isinstance(step.get("action"), str)
                or not isinstance(step.get("args"), dict)):
            return None, "agent_step_unparsed"
        systems = "\n".join(msg["content"] for msg in messages if msg["role"] == "system")
        tool_block = systems.partition("\n도구:\n")[2].partition("\n\n")[0]
        allowed = set(re.findall(r"(?m)^- ([A-Za-z_][\w-]*):", tool_block))
        if not allowed:
            return None, "agent_tool_catalog_missing"
        if step["action"] not in allowed and step["action"] != "stop":
            return None, "agent_step_unknown_tool"
    elif caller.startswith("zzaimy.app.chat_memory."):
        if caller != "zzaimy.app.chat_memory.compact" or tool_calls:
            return None, "unsupported_caller_shape"
        heads = ("지금 목표", "확정된 것", "진행 상황", "남은 일·물어볼 것")
        if any(not re.search(rf"(?m)^{re.escape(head)}\s*:", answer) for head in heads):
            return None, "memo_format"
    elif caller.startswith("zzaimy.app.delegate."):
        if caller not in {"zzaimy.app.delegate._write", "zzaimy.app.delegate.run"} or tool_calls:
            return None, "unsupported_caller_shape"
        from zzaimy.app.delegate import parse_sub
        body, _ = parse_sub(answer)
        if not re.search(r"(?m)^\s*\[본문\]\s*$", answer) or not body.strip():
            return None, "section_format"
    assistant = {"role": "assistant", "content": answer}
    if tool_calls:
        assistant["tool_calls"] = tool_calls
    # Deliberately not named "messages": the SFT runner can read that key
    # directly without checking review status. Promotion must be explicit.
    pair = {"trace_messages": [*messages, assistant], "caller": caller,
            "request": {key: row.get(key) for key in (
                "model", "connection_id", "tools", "response_format", "max_tokens",
                "temperature", "extra_body", "attempt", "asked_max_tokens")},
            "status": "review_pending", "execution_verified": False}
    # Source text is never silently masked into a different training answer.
    from zzaimy.dataset.privacy import protect_candidate
    try:
        protected = protect_candidate(pair)
    except Exception:
        return None, "privacy_check_failed"
    if protected != pair:
        return None, "privacy_review"
    return pair, "accepted_for_review"


def collect(folder: Path) -> tuple[list[dict], list[dict], Counter]:
    counts: Counter = Counter()
    candidates = []
    held = []
    seen = set()
    for path in sorted(folder.glob("llm-*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("not an object")
                except (json.JSONDecodeError, ValueError):
                    counts["invalid_json"] += 1
                    continue
                pair, reason = _candidate(row)
                if pair is None:
                    counts[reason] += 1
                    if reason in {"wrong_model", "unverified_connection", "unrecorded_thinking_mode",
                                  "thinking_enabled", "privacy_check_failed", "privacy_review",
                                  "agent_correction_turn", "agent_step_unparsed",
                                  "agent_tool_catalog_missing", "agent_step_unknown_tool",
                                  "legacy_answer_frame", "unsupported_caller_shape",
                                  "memo_format", "section_format"}:
                        held.append({"reason": reason, "trace": {"file": path.name, "line": number},
                                     "record": row})
                    continue
                fingerprint = hashlib.sha256(json.dumps(
                    {"messages": pair["trace_messages"], "request": pair["request"]},
                    ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                if fingerprint in seen:
                    counts["duplicate"] += 1
                    continue
                seen.add(fingerprint)
                counts[reason] += 1
                pair["sample_id"] = fingerprint
                pair["trace"] = {"file": path.name, "line": number}
                candidates.append(pair)
                counts[f"caller:{pair['caller']}"] += 1
    return candidates, held, counts


def _write_new(path: Path, rows: list[dict]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, required=True,
                        help="One test-only scenario's ZZAIMY_LLM_RECORD folder")
    parser.add_argument("--out", type=Path, required=True, help="New review-candidate JSONL")
    parser.add_argument("--held-out", type=Path, required=True, help="New restricted held-record JSONL")
    args = parser.parse_args()
    if not args.record_dir.is_dir() or not list(args.record_dir.glob("llm-*.jsonl")):
        parser.error("No recorded LLM calls in --record-dir")
    records = list(args.record_dir.glob("llm-*.jsonl"))
    if (args.record_dir.is_symlink() or args.record_dir.stat().st_uid != os.getuid()
            or stat.S_IMODE(args.record_dir.stat().st_mode) & 0o077
            or any(path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()
                   or stat.S_IMODE(path.stat().st_mode) & 0o077 for path in records)):
        parser.error("Recorded prompts need owner-only files (0600) in an owner-only directory (0700)")
    if (args.out.parent != args.held_out.parent or not args.out.parent.is_dir()
            or args.out.parent.stat().st_uid != os.getuid()
            or stat.S_IMODE(args.out.parent.stat().st_mode) & 0o077):
        parser.error("Both outputs need the same existing owner-only (0700) directory")
    if args.out.exists() or args.held_out.exists() or args.out == args.held_out:
        parser.error("Output paths must be distinct and new")
    candidates, held, counts = collect(args.record_dir)
    _write_new(args.out, candidates)
    _write_new(args.held_out, held)
    print(json.dumps({"candidates": len(candidates), "held": len(held), "checks": counts},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
