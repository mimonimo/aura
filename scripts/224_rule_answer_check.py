#!/usr/bin/env python3
"""규정 질문 답의 완결성·출처 링크 — 같은 질문을 지금 코드로 답하게 하고 재는 도구(읽기만, 운영 DB 를 바꾸지 않는다).

에이전트 경로처럼 근거를 먼저 찾는다: 질문 그대로 + 27B 가 만든 규정 검색어마다 규정 조각 4개(문서 번호·조항 표제 함께) →
AgentResponder.answer(evidence=…) 로 답. 재는 것:
- 조항 완결성: 근거에 든 조항 가운데 답이 짚은 조항의 시점·단서 말(app.rule_complete.needed_terms)을 답이 담은 비율
- 출처 링크 비율: 답의 《 》 이름 가운데 근거 목록(last_sources)으로 링크가 되는 것(app.citations.linkify)
- 보충 관문이 돈 횟수, 걸린 시간
고치기 전 코드와 견줄 때는 그 코드 나무를 PYTHONPATH 로 주고 같은 질문 파일로 돌린다(--tag 로 출력 이름을 나눈다).
판정용 함수(rule_complete·citations)는 --judge-src 의 것(새 코드)을 써서 두 판을 같은 잣대로 잰다.

사용(VM, .env.local 필수):
  set -a; . ./.env.local; set +a
  PYTHONPATH=src .venv/bin/python scripts/224_rule_answer_check.py --tag new
  PYTHONPATH=/tmp/old/src .venv/bin/python scripts/224_rule_answer_check.py --tag old --judge-src src
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("RULE_CHECK_ROOT") or Path(__file__).resolve().parent.parent)   # 운영 데이터 자리(코드 나무와 따로 둘 때)

QUESTIONS = [
    "나는 교수고 1970년 7월생이야. 정년퇴직일이 언제야?",
    "우리 대학 직원 정년은 몇 살이고 퇴직일은 언제야?",
    "교원 육아휴직은 몇 년까지 할 수 있어?",
    "연구년(연구안식년) 신청 자격은 어떻게 돼?",
    "국내 출장 여비 규정에서 일비와 숙박비 기준은?",
    "학생은 휴학을 최대 몇 학기까지 할 수 있어?",
    "교직원 명예퇴직 요건과 신청 시기는?",
    "직원 연가는 며칠이야?",
    "계약직원 임용기간은 어떻게 정해져 있어?",
    "교원 재임용 심사는 언제 하고 결과는 언제 알려줘?",
]


def _load(mod: str, src: Path):
    """판정용 모듈을 지정한 코드 나무에서 읽는다(답은 PYTHONPATH 의 코드가 쓰고, 잣대는 같게)."""
    spec = importlib.util.spec_from_file_location(f"judge_{mod}", src / "zzaimy" / "app" / f"{mod}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--judge-src", default="src")
    ap.add_argument("--n", type=int, default=len(QUESTIONS))
    ap.add_argument("--old-evidence", action="store_true", help="고치기 전 에이전트처럼 규정 근거에 문서 번호·조항 표제를 싣지 않는다")
    a = ap.parse_args()
    from zzaimy.app.db import Database
    from zzaimy.app.regulations import find_relevant
    from zzaimy.app.responder import AgentResponder, rule_queries
    judge = Path(a.judge_src).resolve()
    rc, cit = _load("rule_complete", judge), _load("citations", judge)
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    scope = {"role": "staff", "levels": ("public", "internal", "staff")}
    rows = []
    for q in QUESTIONS[: a.n]:
        ev, seen = [], set()
        for x in [q] + rule_queries(q, []):
            for h in find_relevant(db, x, top_k=4, levels=scope["levels"]):
                k = (h.get("reg_title"), h.get("heading"))
                if k in seen:
                    continue
                seen.add(k)
                ev.append({"title": h.get("reg_title") or "규정", "text": h.get("content") or ""} if a.old_evidence else
                          {"title": h.get("reg_title") or "규정", "text": h.get("content") or "", "doc_id": h.get("doc_id"),
                           "note": h.get("heading") or "", "rule": True})
        ar = AgentResponder()
        t0 = time.time()
        ans = ar.answer(db, q, scope=scope, evidence=ev)
        sec = round(time.time() - t0, 1)
        ctx = "\n\n".join(f"《{e['title']}》 · {e.get('note', '')}\n{e['text']}" for e in ev)
        arts = [x for x in rc.articles(ctx) if rc.cited(ans, x)]
        need = sum(len(rc.needed_terms(x["text"])) for x in arts)
        lack = sum(len(g["terms"]) for g in rc.missing(ans, ctx))
        names = re.findall(r"《([^》\n]{2,120})》", ans)
        html = cit.linkify(ans, getattr(ar, "last_sources", []) or [])
        linked = sum(1 for n in names if re.search(r'title="근거 문서 열기">' + re.escape(n.strip()), html))
        rows.append({"q": q, "sec": sec, "cited_articles": len(arts), "need": need, "lack": lack,
                     "names": len(names), "linked": linked, "supplement": bool((ar.last_timing or {}).get("rule_supplement")),
                     "evidence": len(ev), "answer": ans})
        print(f"{sec:6.1f}s 조항 {len(arts)} 시점·단서 {need - lack}/{need} 링크 {linked}/{len(names)}"
              f"{' 보충' if rows[-1]['supplement'] else ''} | {q}", flush=True)
    need = sum(r["need"] for r in rows)
    lack = sum(r["lack"] for r in rows)
    names = sum(r["names"] for r in rows)
    linked = sum(r["linked"] for r in rows)
    summary = {"tag": a.tag, "n": len(rows), "terms_kept": f"{need - lack}/{need}", "links": f"{linked}/{names}",
               "supplements": sum(r["supplement"] for r in rows), "avg_sec": round(sum(r["sec"] for r in rows) / max(1, len(rows)), 1)}
    out = Path(os.environ.get("RULE_CHECK_OUT") or ROOT / "data" / "eval") / f"rule_answer_check_{a.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RULE_ANSWER_CHECK", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
