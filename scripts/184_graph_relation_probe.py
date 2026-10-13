#!/usr/bin/env python3
"""그래프 단계 실측 — 관계를 묻는 질문에서 검색(grant_search)이 관계의 양쪽 문서를 근거로 가져오나.

질문은 사람이 고르지 않는다. 그래프의 관계에서 뽑는다.
- 계획↔실적(plans_reports): 짝인 계획서·실적보고서 한 쌍 → 「<사업> <연차> 「<계획서 절>」 계획 대비 실적을 비교해 줘」.
  정답: 두 문서가 모두 상위에(pair), 각각(plan·report)
- 앞·뒤 단계·편입(succeeded_by·integrated_into): 한 사업의 문서 절 → 관계 건너편 사업의 이름으로 묻는다
  (「<다음 단계 사업>의 전신 사업에서는 「<절>」을 어떻게 했나」). 정답: 그 절이 든 사업의 문서가 상위에
- 평가(evaluates): 평가 문서의 절 → 「<사업> <연차> 실적보고서가 받은 「<절>」 의견」. 정답: 평가 문서가 상위에
같은 질문을 두 번 잰다 — 이름 맞추기(예전)와 모델 읽기(question_read). 결과: data/eval/graph_relation_probe_<tag>.json
과 요약 한 줄(GRAPH_REL_PROBE). 질문 집합은 data/eval/graph_relation_set.json 에 두고 다시 쓴다(--new-set 이면 새로).

사용(VM, .env.local 필수): scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/184_graph_relation_probe.py
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

_SENT = re.compile(r"(?:다|함|음|임|됨|니다)\s*[.。]?\s*$|[:：,，]\s*$|\d\s*$")
_OUTLINE = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]?|\d{1,2}(?:[.\-]\d{1,2})*[.．)]|[가-하][.．)]|[(（]\d{1,2}[)）])\s*")


def _heading(t: str) -> str:
    t = _OUTLINE.sub("", " ".join((t or "").split()))
    ok = 4 <= len(t) <= 30 and not _SENT.search(t) and len(re.findall(r"[가-힣A-Za-z]", t)) >= 3
    return t if ok else ""


def _sections(conn, doc_id: int) -> list[str]:
    rows = conn.execute("SELECT label FROM kg_nodes WHERE type = 'section' AND doc_id = ? LIMIT 80", (doc_id,)).fetchall()
    return [h for (lab,) in rows if (h := _heading(lab))]


def _where(conn, doc_id: int) -> tuple[str, str]:
    """문서의 사업 이름·연차 이름(그래프의 contains)."""
    prog, year = "", ""
    for (src,) in conn.execute("SELECT src FROM kg_edges WHERE kind = 'contains' AND dst = ?", (f"doc:{doc_id}",)).fetchall():
        if src.startswith("year:"):
            year = (conn.execute("SELECT label FROM kg_nodes WHERE id = ?", (src,)).fetchone() or [""])[0]
            src = (conn.execute("SELECT src FROM kg_edges WHERE kind = 'contains' AND dst = ? AND src LIKE 'program:%'",
                                (src,)).fetchone() or [""])[0]
        if src.startswith("program:"):
            prog = (conn.execute("SELECT label FROM kg_nodes WHERE id = ?", (src,)).fetchone() or [""])[0]
    return prog, year


def make_set(db, seed: int, n_pair: int, n_lineage: int, n_eval: int) -> list[dict]:
    rnd = random.Random(seed)
    out: list[dict] = []
    with db._conn() as conn:
        pairs = [(a, b) for a, b in conn.execute(
            "SELECT src, dst FROM kg_edges WHERE kind = 'plans_reports' AND src NOT LIKE '%:sec:%' AND dst NOT LIKE '%:sec:%'")]
        rnd.shuffle(pairs)
        for a, b in pairs:
            if sum(x["type"] == "plan_report" for x in out) >= n_pair:
                break
            pa, pb = int(a.split(":")[1]), int(b.split(":")[1])
            secs = _sections(conn, pa)
            prog, year = _where(conn, pa)
            if not secs or not prog or not year:
                continue
            out.append({"type": "plan_report", "q": f"{prog} {year} 「{rnd.choice(secs)}」 계획 대비 실적을 비교해 줘",
                        "gold": [pa, pb]})
        lineage = [(a, b, k) for a, b, k in conn.execute(
            "SELECT src, dst, kind FROM kg_edges WHERE kind IN ('succeeded_by', 'integrated_into')")]
        per = max(1, n_lineage // max(1, len(lineage) * 2))
        for a, b, kind in lineage:
            for target, asked, how in ((a, b, "전신 사업" if kind == "succeeded_by" else "편입된 사업"),
                                       (b, a, "후속 사업" if kind == "succeeded_by" else "편입해 간 사업")):
                asked_label = (conn.execute("SELECT label FROM kg_nodes WHERE id = ?", (asked,)).fetchone() or [""])[0]
                docs = [int(r[0]) for r in conn.execute(
                    "SELECT n.doc_id FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst WHERE e.kind = 'contains' AND n.doc_id IS NOT NULL"
                    " AND (e.src = ? OR e.src IN (SELECT dst FROM kg_edges WHERE kind = 'contains' AND src = ? AND dst LIKE 'year:%'))",
                    (target, target)).fetchall()]
                gold = set(docs)
                rnd.shuffle(docs)
                made = 0
                for d in docs[:40]:
                    secs = _sections(conn, d)
                    if not secs or not asked_label:
                        continue
                    out.append({"type": "lineage", "q": f"{asked_label}의 {how}에서는 「{rnd.choice(secs)}」을 어떻게 했나",
                                "gold": sorted(gold), "any": True})
                    made += 1
                    if made >= per:
                        break
        evals = [(a, b) for a, b in conn.execute(
            "SELECT src, dst FROM kg_edges WHERE kind = 'evaluates' AND src NOT LIKE '%:sec:%' AND dst NOT LIKE '%:sec:%'")]
        rnd.shuffle(evals)
        for a, b in evals:
            if sum(x["type"] == "evaluates" for x in out) >= n_eval:
                break
            ea, eb = int(a.split(":")[1]), int(b.split(":")[1])
            secs = _sections(conn, ea)
            prog, year = _where(conn, eb)
            if not secs or not prog:
                continue
            out.append({"type": "evaluates", "q": f"{prog} {year} 문서가 받은 「{rnd.choice(secs)}」 의견을 알려줘".replace("  ", " "),
                        "gold": [ea]})
    return out


def run(db, items: list[dict], k: int, reader) -> list[dict]:
    from zzaimy.app import grant_search
    rows = []
    for it in items:
        t0 = time.time()
        try:
            g = grant_search.search(db, it["q"], k=k, user=None, reader=reader)
            got = [h["doc_id"] for h in g["hits"]]
            err = ""
        except Exception as e:
            got, err = [], f"{type(e).__name__}: {e}"[:200]
        gold = set(it["gold"])
        ok = bool(gold & set(got)) if it.get("any") else gold <= set(got)
        rows.append({**it, "got": got, "ok": ok, "sec": round(time.time() - t0, 2), "error": err,
                     "plan_ok": it["type"] == "plan_report" and it["gold"][0] in got,
                     "report_ok": it["type"] == "plan_report" and it["gold"][1] in got})
    return rows


def summary(rows: list[dict]) -> dict:
    out = {"n": len(rows), "ok": round(sum(r["ok"] for r in rows) / max(1, len(rows)), 3),
           "sec_median": round(statistics.median([r["sec"] for r in rows]), 2) if rows else None,
           "errors": sum(bool(r["error"]) for r in rows)}
    for t in ("plan_report", "lineage", "evaluates"):
        part = [r for r in rows if r["type"] == t]
        if part:
            out[t] = {"n": len(part), "ok": round(sum(r["ok"] for r in part) / len(part), 3)}
    pr = [r for r in rows if r["type"] == "plan_report"]
    if pr:
        out["plan_report"].update(plan=round(sum(r["plan_ok"] for r in pr) / len(pr), 3),
                                  report=round(sum(r["report_ok"] for r in pr) / len(pr), 3))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pairs", type=int, default=30)
    ap.add_argument("--lineage", type=int, default=20)
    ap.add_argument("--evals", type=int, default=10)
    ap.add_argument("--tag", default=time.strftime("%Y%m%d%H%M"))
    ap.add_argument("--set", default=str(ROOT / "data/eval/graph_relation_set.json"))
    ap.add_argument("--new-set", action="store_true")
    ap.add_argument("--only", choices=["name", "model"], default=None, help="한쪽만 잰다")
    args = ap.parse_args()
    import os
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    sp = Path(args.set)
    if sp.is_file() and not args.new_set:
        items = json.loads(sp.read_text(encoding="utf-8"))["items"]
    else:
        items = make_set(db, args.seed, args.pairs, args.lineage, args.evals)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "seed": args.seed, "items": items},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    from zzaimy.graph.question_read import reader_for
    result = {"made": time.strftime("%Y-%m-%d %H:%M"), "k": args.k, "set": str(sp.name)}
    if args.only != "model":
        rows = run(db, items, args.k, None)
        result["name"] = {"summary": summary(rows), "rows": rows}
    if args.only != "name":
        rows = run(db, items, args.k, reader_for("answer", force=True))
        result["model"] = {"summary": summary(rows), "rows": rows}
    out = ROOT / "data" / "eval" / f"graph_relation_probe_{args.tag}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("GRAPH_REL_PROBE " + json.dumps({k: v["summary"] for k, v in result.items() if isinstance(v, dict) and "summary" in v},
                                          ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
