#!/usr/bin/env python3
"""온톨로지 점검 — 지식 그래프(kg_nodes·kg_edges)가 지켜야 할 규칙을 기계로 센다. 고치지 않는다(읽기만).

규칙:
1. 문서 하나는 사업 하나(연차 경유 포함)에만 속한다
2. 연차 노드의 연도는 그 사업의 기간(장부) 안이다
3. 사업은 분류(program_group)에 속하거나, 장부 확인이 없다는 표시가 남는다 — 분류 없는 사업 목록
4. 앞 단계 → 다음 단계(succeeded_by)·편입(integrated_into)은 순환하지 않고, 양 끝이 사업 노드다
5. 사업 이름이 막연하지 않다(「재정지원 사업」「지자체 연계 사업」처럼 고유한 낱말이 없는 이름) — 후보 목록
6. 사업마다 문서 수 — 문서가 없거나 한두 건뿐인 사업(장부에만 있는 것과 문서에서 잘못 선 것을 가린다)
7. 관계마다 기준(basis)과 근거(evidence)가 있다(kg_store 원칙)
8. 계획↔실적 짝(plans_reports)의 양 끝이 같은 사업이다

출력: data/eval/ontology_audit_<tag>.json 과 요약 줄(ONTOLOGY_AUDIT).
사용(VM): scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/174_ontology_audit.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

# 사업 이름에서 흔한 꼬리·일반어를 빼고 남는 고유 낱말이 없으면 막연한 이름이다
_GENERIC = re.compile(r"사업단?|지원|육성|운영|활성화|재정|국고|정부|교육부|지자체|연계|대학|전문대학|기본|일반|공통|기타|및|등|의|"
                      r"[0-9]+|[()·\s\-+.,]")
_PERIOD = re.compile(r"((?:19|20)\d{2})\s*[~∼\-]\s*((?:19|20)\d{2})?")


def vague(label: str) -> bool:
    from zzaimy.graph.programs import vague_name
    return vague_name(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=time.strftime("%Y%m%d%H%M"))
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    with db._conn() as conn:
        progs = {r[0]: {"label": r[1], "props": json.loads(r[2] or "{}")} for r in conn.execute(
            "SELECT id, label, props FROM kg_nodes WHERE type = 'program'").fetchall()}
        years = {r[0]: {"label": r[1], "props": json.loads(r[2] or "{}")} for r in conn.execute(
            "SELECT id, label, props FROM kg_nodes WHERE type = 'year'").fetchall()}
        groups = {r[0]: r[1] for r in conn.execute("SELECT id, label FROM kg_nodes WHERE type = 'program_group'").fetchall()}
        contains = [(r[0], r[1]) for r in conn.execute(
            "SELECT src, dst FROM kg_edges WHERE kind = 'contains' AND (src LIKE 'program:%' OR src LIKE 'year:%' OR src LIKE 'group:%')"
            " AND dst NOT LIKE '%:sec:%'").fetchall()]
        rel = [(r[0], r[1], r[2]) for r in conn.execute(
            "SELECT src, dst, kind FROM kg_edges WHERE kind IN ('succeeded_by', 'integrated_into')").fetchall()]
        no_basis = conn.execute("SELECT kind, COUNT(*) FROM kg_edges WHERE basis = '' OR evidence = '[]' OR evidence = ''"
                                " GROUP BY kind").fetchall()
        pr = [(r[0], r[1]) for r in conn.execute("SELECT src, dst FROM kg_edges WHERE kind = 'plans_reports'").fetchall()]
    year_prog = {d: s for s, d in contains if s in progs and d.startswith("year:")}
    doc_progs: dict[str, set[str]] = defaultdict(set)
    for s, d in contains:
        if d.startswith("doc:"):
            p = s if s in progs else year_prog.get(s)
            if p:
                doc_progs[d].add(p)
    multi = {d: sorted(ps) for d, ps in doc_progs.items() if len(ps) > 1}
    n_docs = Counter(p for ps in doc_progs.values() for p in ps)
    # 2. 연차 연도 ⊂ 사업 기간
    out_of_period = []
    for y, p in year_prog.items():
        led = progs[p]["props"].get("ledger") or {}
        m = _PERIOD.search(str(led.get("period") or ""))
        yr = years.get(y, {}).get("props", {}).get("year")
        if m and yr:
            lo, hi = int(m.group(1)), int(m.group(2)) if m.group(2) else 9999
            if not lo <= int(yr) <= hi:
                out_of_period.append({"year": y, "label": years[y]["label"], "period": led.get("period")})
    # 3. 분류
    in_group = {d for s, d in contains if s in groups and d in progs}
    ungrouped = sorted(((p, progs[p]["label"], n_docs.get(p, 0)) for p in progs if p not in in_group), key=lambda x: -x[2])
    # 4. 순환·끝점
    bad_end = [(s, d, k) for s, d, k in rel if s not in progs or d not in progs]
    nxt = defaultdict(list)
    for s, d, k in rel:
        if k == "succeeded_by":
            nxt[s].append(d)
    cycles = []
    for start in nxt:
        seen, cur = {start}, list(nxt[start])
        while cur:
            x = cur.pop()
            if x == start:
                cycles.append(start)
                break
            if x not in seen:
                seen.add(x)
                cur.extend(nxt.get(x, []))
    # 5. 막연한 이름 · 6. 문서 수
    vague_names = sorted(((p, progs[p]["label"], n_docs.get(p, 0)) for p in progs if vague(progs[p]["label"])), key=lambda x: -x[2])
    thin = sorted(((p, progs[p]["label"], n_docs.get(p, 0)) for p in progs if n_docs.get(p, 0) <= 2), key=lambda x: x[2])
    # 8. 짝의 사업
    def prog_of_node(x: str) -> set[str]:
        return doc_progs.get(x.split(":sec:")[0], set())
    pr_cross = [(a, b) for a, b in pr if prog_of_node(a) and prog_of_node(b) and not (prog_of_node(a) & prog_of_node(b))]
    summary = {"programs": len(progs), "groups": len(groups), "docs_assigned": len(doc_progs),
               "multi_program_docs": len(multi), "years_out_of_period": len(out_of_period),
               "ungrouped_programs": len(ungrouped), "relation_bad_ends": len(bad_end), "succession_cycles": len(cycles),
               "vague_program_names": len(vague_names), "thin_programs(<=2 docs)": len(thin),
               "edges_without_basis": {k: n for k, n in no_basis}, "plan_report_cross_program": len(pr_cross),
               "relations": Counter(k for _s, _d, k in rel)}
    out = ROOT / "data" / "eval" / f"ontology_audit_{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "multi": dict(list(multi.items())[:50]), "out_of_period": out_of_period[:50],
                               "ungrouped": ungrouped, "bad_end": bad_end, "cycles": cycles, "vague": vague_names, "thin": thin,
                               "programs_by_docs": sorted(((p, progs[p]["label"], n_docs.get(p, 0)) for p in progs), key=lambda x: -x[2]),
                               "plan_report_cross": pr_cross[:50]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ONTOLOGY_AUDIT", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
