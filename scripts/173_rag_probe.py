#!/usr/bin/env python3
"""사업 문서 RAG 실측 — 그래프에서 시험 질문을 뽑아 검색(grant_search)과 답변(AgentResponder, 27B)을 실제로 돌린다.

질문은 사람이 고르지 않는다. 사업에 배정된 계획서·실적보고서에서 본문이 있는 절을 무작위로 골라
「<사업> <연도> <갈래>의 「<절 제목>」 내용을 알려줘」로 만들고, 그 절이 든 문서를 정답으로 둔다(정답 채우기가 아니라 검사용).
재는 것:
- 검색: 정답 문서가 상위 k 안에 있나(doc@k), 같은 사업 문서가 있나(prog@k), 걸린 시간
- 답변(--answer N): 빈 답·오류, 답의 숫자가 건넨 근거에 없는 것(지어낸 수치 의심), 정답 절 글과 겹치는 낱말 비율
출력: data/eval/rag_probe_<tag>.json 과 요약 한 줄(RAG_PROBE).

사용(VM, .env.local 필수): scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/173_rag_probe.py --n 40 --answer 10
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import kg_store  # noqa: E402

KIND_KO = {"plan": "계획서", "report": "실적보고서"}
_NUM = re.compile(r"(?<![\d.])\d{2,}(?:[,.]\d+)*")
_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_OUTLINE = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]?|\d{1,2}(?:[.\-]\d{1,2})*[.．)]|[가-하][.．)]|[(（]\d{1,2}[)）])\s*")


_SENT = re.compile(r"(?:다|함|음|임|됨|니다)\s*[.。]?\s*$|[:：,，]\s*$|^(?:위|아래|상기)\s|끝\.?$|\d\s*$")


def _HEADING_OK(t: str) -> bool:
    """시험 질문이 될 만한 절 제목 — 문장·조각(「위 관련 근거에 의거, 2」「구입 필요성 :」)이 아닌 4~30자 명사구."""
    return 4 <= len(t) <= 30 and not _SENT.search(t) and len(re.findall(r"[가-힣A-Za-z]", t)) >= 3


def probes(db, n: int, seed: int) -> list[dict]:
    nodes = {x["id"]: x for x in kg_store.nodes(db, "program")}
    nodes.update({x["id"]: x for x in kg_store.nodes(db, "year")})
    docs = {x["id"]: x for x in kg_store.nodes(db, "doc")}
    prog_of: dict[str, str] = {}
    contains = kg_store.edges(db, "contains")
    for e in contains:
        if e["src"] in nodes and nodes[e["src"]]["type"] == "program":
            prog_of[e["dst"]] = e["src"]
    for e in contains:
        if e["src"].startswith("year:") and e["src"] in prog_of:
            prog_of[e["dst"]] = prog_of[e["src"]]
    pool = [d for d in docs.values() if d["id"] in prog_of and d["props"].get("kind") in KIND_KO
            and not d["props"].get("other_org") and d.get("doc_id")]
    rng = random.Random(seed)
    rng.shuffle(pool)
    out: list[dict] = []
    per_prog: dict[str, int] = {}
    cap = max(2, n // 6)                                   # 한 사업에 쏠리지 않게
    for d in pool:
        if len(out) >= n:
            break
        pid = prog_of[d["id"]]
        if per_prog.get(pid, 0) >= cap:
            continue
        did = int(d["doc_id"])
        with db._conn() as conn:
            secs = [dict(r) for r in conn.execute("SELECT label, props FROM kg_nodes WHERE type = 'section' AND doc_id = ?",
                                                  (did,)).fetchall()]
        by_seq = {c["seq"]: str(c.get("content") or "") for c in db.list_doc_chunks(did) if c.get("kind") == "text"}
        cands = []
        for sec in secs:
            title = _OUTLINE.sub("", (sec["label"] or "").strip())[:40]
            body = "\n".join(by_seq.get(q, "") for q in (json.loads(sec["props"] or "{}").get("chunks") or []))
            if len(title) >= 4 and len(body) >= 300 and _HEADING_OK(title):
                cands.append((title, body))
        if not cands:
            continue
        title, body = rng.choice(cands)
        year = d["props"].get("year")
        q = f"{nodes[pid]['label']} {str(year) + '년 ' if year else ''}{KIND_KO[d['props']['kind']]}의 「{title}」 내용을 알려줘"
        out.append({"q": q, "doc_id": did, "program": pid, "gold": body[:3000],
                    "prog_docs": None})
        per_prog[pid] = per_prog.get(pid, 0) + 1
    # 같은 사업의 문서 번호(prog@k 판정)
    under: dict[str, set[int]] = {}
    for d in docs.values():
        p = prog_of.get(d["id"])
        if p and d.get("doc_id"):
            under.setdefault(p, set()).add(int(d["doc_id"]))
    for p in out:
        p["prog_docs"] = under.get(p["program"], set())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--answer", type=int, default=0, help="앞에서 몇 개를 27B 답변까지")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tag", default=time.strftime("%Y%m%d%H%M"))
    args = ap.parse_args()
    from zzaimy.app import grant_search
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    items = probes(db, args.n, args.seed)
    rows = []
    for i, p in enumerate(items):
        t0 = time.time()
        err = ""
        try:
            g = grant_search.search(db, p["q"], k=args.k, user=None)
        except Exception as e:                              # 검색 실패도 결과다
            g, err = {"hits": [], "steps": []}, f"{type(e).__name__}: {e}"[:200]
        dt = time.time() - t0
        hit_docs = [h["doc_id"] for h in g["hits"]]
        row = {"q": p["q"], "doc_id": p["doc_id"], "program": p["program"], "hits": hit_docs, "sec": round(dt, 2),
               "doc_hit": p["doc_id"] in hit_docs, "prog_hit": any(h in p["prog_docs"] for h in hit_docs),
               "steps": g.get("steps", [])[:4], "error": err}
        if i < args.answer:
            from zzaimy.app.responder import AgentResponder
            t1 = time.time()
            try:
                ar = AgentResponder()
                ans = ar.answer(db, p["q"], scope={"user": None})
                ctx = getattr(ar, "last_context", "") or " ".join(h["content"] for h in g["hits"])
                qn = set(_NUM.findall(p["q"]))           # 질문에 있던 숫자(연도 등)는 근거 밖이 아니다
                nums = [x for x in _NUM.findall(ans) if x not in qn and x not in ctx
                        and x.replace(",", "") not in ctx.replace(",", "")]
                gw, aw = set(_WORD.findall(p["gold"])), set(_WORD.findall(ans))
                row.update({"answer": ans[:1500], "answer_sec": round(time.time() - t1, 1), "unsupported_numbers": nums[:10],
                            "gold_overlap": round(len(gw & aw) / max(len(aw), 1), 3), "answer_error": ""})
            except Exception as e:
                row.update({"answer": "", "answer_error": f"{type(e).__name__}: {e}"[:200]})
        rows.append(row)
        print(f"[{i + 1}/{len(items)}] doc {'O' if row['doc_hit'] else 'X'} prog {'O' if row['prog_hit'] else 'X'} "
              f"{row['sec']}s {p['q'][:60]}", flush=True)
    n = len(rows) or 1
    ans_rows = [r for r in rows if "answer" in r]
    summary = {"n": len(rows), "k": args.k,
               "doc_at_k": round(sum(r["doc_hit"] for r in rows) / n, 3),
               "prog_at_k": round(sum(r["prog_hit"] for r in rows) / n, 3),
               "search_errors": sum(bool(r["error"]) for r in rows),
               "empty_hits": sum(not r["hits"] for r in rows),
               "search_sec_median": round(statistics.median([r["sec"] for r in rows]), 2) if rows else None,
               "answers": len(ans_rows),
               "answer_errors": sum(bool(r.get("answer_error")) for r in ans_rows),
               "answers_with_unsupported_numbers": sum(bool(r.get("unsupported_numbers")) for r in ans_rows),
               "gold_overlap_median": round(statistics.median([r["gold_overlap"] for r in ans_rows if "gold_overlap" in r]), 3)
               if any("gold_overlap" in r for r in ans_rows) else None}
    out = ROOT / "data" / "eval" / f"rag_probe_{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RAG_PROBE", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
