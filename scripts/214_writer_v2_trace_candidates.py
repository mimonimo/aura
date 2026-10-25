#!/usr/bin/env python3
"""Make review candidates from one isolated Writer scenario's LLM call log.

This does not approve or split training data. Keep the input and output on the
training machine; recorded prompts can contain complete source documents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def _candidate(row: dict) -> tuple[dict | None, str]:
    caller = str(row.get("caller") or "")
    if not caller.startswith("zzaimy.") or caller.startswith("zzaimy.eval."):
        return None, "unrelated_caller"
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
    if not answer.strip() and not tool_calls:
        return None, "empty_answer"
    if row.get("finish") not in ({"stop", "tool_calls"} if tool_calls else {"stop"}):
        return None, "unfinished_answer"
    assistant = {"role": "assistant", "content": answer}
    if tool_calls:
        assistant["tool_calls"] = tool_calls
    pair = {"messages": [*messages, assistant], "caller": caller,
            "request": {key: row.get(key) for key in (
                "model", "tools", "response_format", "max_tokens", "temperature", "extra_body")},
            "status": "review_pending"}
    # Source text is never silently masked into a different training answer.
    from zzaimy.dataset.privacy import protect_candidate
    if protect_candidate(pair) != pair:
        return None, "privacy_review"
    return pair, "accepted_for_review"


def collect(folder: Path) -> tuple[list[dict], Counter]:
    counts: Counter = Counter()
    candidates = []
    seen = set()
    for path in sorted(folder.glob("llm-*.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
                continue
            fingerprint = hashlib.sha256(json.dumps(
                {"messages": pair["messages"], "request": pair["request"]},
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
    return candidates, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, required=True,
                        help="One test-only scenario's ZZAIMY_LLM_RECORD folder")
    parser.add_argument("--out", type=Path, required=True, help="New review-candidate JSONL")
    args = parser.parse_args()
    if not args.record_dir.is_dir() or not list(args.record_dir.glob("llm-*.jsonl")):
        parser.error("No recorded LLM calls in --record-dir")
    candidates, counts = collect(args.record_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        for candidate in candidates:
            stream.write(json.dumps(candidate, ensure_ascii=False) + "\n")
    print(json.dumps({"candidates": len(candidates), "checks": counts}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
