#!/usr/bin/env python3
"""대화 답 걸린 시간 보고 — doc_agent_episodes.jsonl 의 answer_timing 을 대화·시간대별로 읽어 표로 낸다(읽기만).

11-10 동시 4건 실측(대화 173~176)에서 간단한 질문이 96~428초 걸린 원인을 가른 도구. 생각 켬 여부·생성 토큰·생성 시간·초당 토큰·
에이전트 걸음 수·답을 쓴 서버를 함께 보여, 고친 뒤(생각 켜기 판단·두 서버 나누기) 전후를 같은 잣대로 비교한다.

  python3 scripts/answer_timing_report.py ~/aura/data/doc_agent_episodes.jsonl --since 2026-11-10T08:00 [--until ...] [--session 173 174]
  (운영 VM 에서: python3 scripts/answer_timing_report.py "$ZZAIMY_DATA_DIR/doc_agent_episodes.jsonl" --since 2026-11-10T08:00)"""
from __future__ import annotations

import argparse
import json
import statistics
import sys


def rows(path: str, since: str = "", until: str = "", sessions: set[str] | None = None):
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            at = str(r.get("at") or "")
            if (since and at < since) or (until and at > until):
                continue
            if sessions and str(r.get("session")) not in sessions:
                continue
            t = r.get("answer_timing") or {}
            if not t:
                continue
            toks = t.get("completion_tokens")
            gen = t.get("generate_sec")
            yield {
                "at": at, "session": r.get("session"), "steps": len(r.get("steps") or []),
                "think": bool(t.get("think")), "prompt_tokens": t.get("prompt_tokens"), "completion_tokens": toks,
                "generate_sec": gen, "search_sec": t.get("search_sec"), "total_sec": t.get("total_sec"),
                "tok_s": round(toks / gen, 1) if isinstance(toks, (int, float)) and isinstance(gen, (int, float)) and gen > 0 else None,
                "server": t.get("server") or "", "request": str(r.get("request") or "")[:40].replace("\n", " "),
            }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--session", nargs="*", default=None)
    ap.add_argument("--json", action="store_true", help="표 대신 JSON 줄")
    a = ap.parse_args(argv)
    rs = list(rows(a.path, a.since, a.until, set(a.session) if a.session else None))
    if a.json:
        for r in rs:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    cols = ["at", "session", "steps", "think", "prompt_tokens", "completion_tokens", "generate_sec", "tok_s", "total_sec", "server", "request"]
    print("\t".join(cols))
    for r in rs:
        print("\t".join("" if r[c] is None else str(r[c]) for c in cols))
    if rs:
        def med(k, sub):
            v = [r[k] for r in sub if isinstance(r[k], (int, float))]
            return round(statistics.median(v), 1) if v else "-"
        print()
        for label, sub in (("생각 켬", [r for r in rs if r["think"]]), ("생각 끔", [r for r in rs if not r["think"]])):
            if sub:
                print(f"{label} {len(sub)}건: 생성 토큰 중앙 {med('completion_tokens', sub)}, 생성 {med('generate_sec', sub)}초, "
                      f"{med('tok_s', sub)} tok/s, 전체 {med('total_sec', sub)}초, 걸음 {med('steps', sub)}")
        by = {}
        for r in rs:
            by[r["server"] or "모름"] = by.get(r["server"] or "모름", 0) + 1
        print("서버별 건수: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
