#!/usr/bin/env python3
"""온톨로지 v3 그래프를 만든다 — 사업 → 연차 → 문서 → 절, 관계마다 기준·근거(ADR-0048).

  사업 분류(graph/programs): 문서 제목·앞머리의 사업명·약칭으로 사업 카드와 문서별 사업·연차·갈래(근거·검토 대기 포함).
  절 트리(graph/sections): 저장된 제목 조각으로 목차를 세우고 조각을 절에 매단다.
  관계: contains(분류·구조), plans_reports(같은 사업·연차의 계획↔실적 — 문서, 그리고 제목이 같은 절),
        evaluates(평가 결과 → 같은 연차 문서), continues(다음 연차 계획서의 같은 절 — 연차 비교).

사용(VM, .env.local 을 읽고):
  env PYTHONPATH=src .venv/bin/python scripts/157_build_kg.py --docs 557-585            # 미리 보기(분류·절·관계 수)
  ... --apply                                                                              # kg_nodes·kg_edges 에 쓴다
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import kg_store, programs, sections  # noqa: E402

KIND_LABEL = {"plan": "계획서", "report": "실적보고서", "evaluation": "평가 결과", "form": "양식", "criteria": "평가 기준",
              "announcement": "공고", "basic_plan": "기본계획", "guideline": "지침·매뉴얼", "regulation": "규정"}


def _ids(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True, help="문서 id 목록(예: 557-585,601)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--generic-parent", type=float, default=0.5, help="흔한 반복 제목의 바로 위 절 제목 겹침 하한(0 이면 끔)")
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))

    docs, chunk_map = [], {}
    for did in _ids(args.docs):
        d = db.get_document(did)
        if not d:
            continue
        chunks = db.list_doc_chunks(did)
        chunk_map[did] = chunks
        head = "\n".join(str(c["content"]) for c in chunks[:30])
        proj = db.get_project(int(d["project_id"])) if d.get("project_id") else None
        # 문서함 프로젝트(담당자가 정한 소속)는 폴더 경로처럼 강한 근거다
        docs.append({"id": did, "filename": d["filename"], "head": head, "path": (proj or {}).get("name", "")})
    cards = programs.build_cards(docs)
    assigns = {a.doc_id: a for a in programs.classify(docs, cards)}
    used = {a.program for a in assigns.values() if a.program}
    cards = [c for c in cards if c.node_id in used]

    print("== 사업 카드")
    for c in cards:
        print(f"  {c.node_id}  「{c.name}」  다른 이름 {sorted(set(c.names) | set(c.acrs))[:6]}")
    print("== 문서 분류")
    for d in docs:
        a = assigns[d["id"]]
        print(f"  #{d['id']:<4} {a.status:6} {a.program or '-':22} {a.round or '-'}차 {a.year or '-'} {KIND_LABEL.get(a.kind, a.kind or '?'):6}"
              f" 몫 {a.share:.2f}  {d['filename'][:34]}  ← {'; '.join(a.evidence[:2])}")

    nodes: list[tuple] = []
    edges: list[tuple] = []
    by_year: dict[str, list[int]] = defaultdict(list)
    for c in cards:
        nodes.append((c.node_id, "program", c.name, {"names": sorted(c.names), "acronyms": sorted(c.acrs)}, None))
    for d in docs:
        a = assigns[d["id"]]
        dnode = f"doc:{d['id']}"
        nodes.append((dnode, "doc", d["filename"], {"kind": a.kind, "kind_label": KIND_LABEL.get(a.kind, a.kind), "year": a.year,
                                                    "round": a.round, "share": a.share, "status": a.status,
                                                    "evidence": a.evidence}, d["id"]))
        if a.program:
            tag = f"{a.round}차년도" if a.round else (str(a.year) if a.year else "")
            if tag:
                ynode = f"year:{a.program.split(':', 1)[1]}:" + (f"r{a.round}" if a.round else f"y{a.year}")
                label = f"{a.program_name} {tag}" + (f" ({a.year})" if a.round and a.year else "")
                nodes.append((ynode, "year", label, {"round": a.round, "year": a.year}, None))
                edges.append((a.program, ynode, "contains", "분류", [f"문서 #{d['id']} 분류: " + "; ".join(a.evidence[:2])]))
                edges.append((ynode, dnode, "contains", "분류", a.evidence[:3] or ["분류"]))
                by_year[ynode].append(d["id"])
            else:
                edges.append((a.program, dnode, "contains", "분류", a.evidence[:3] or ["분류"]))
        secs = sections.build(chunk_map[d["id"]])
        d["sections"] = secs
        for s in secs:
            snode = f"{dnode}:sec:{s.path}"
            nodes.append((snode, "section", s.title, {"level": s.level, "seq": s.seq, "chunks": s.chunks}, d["id"]))
            parent = f"{dnode}:sec:{s.parent}" if s.parent else dnode
            edges.append((parent, snode, "contains", "구조", [f"목차: {s.title}"]))
    docs_by_id = {d["id"]: d for d in docs}
    content = {(did, int(c["seq"])): str(c["content"]) for did, chunks in chunk_map.items() for c in chunks}
    sec_doc = {id(s): d["id"] for d in docs for s in d["sections"]}

    def text_of(s) -> str:
        """절 본문 앞부분(낱말 겹침용)."""
        return " ".join(content.get((sec_doc.get(id(s)), q), "")[:300] for q in s.chunks[:6])
    # 같은 사업·연차의 계획 ↔ 실적, 평가 → 대상
    for ynode, ids in by_year.items():
        plans = [i for i in ids if assigns[i].kind == "plan"]
        reports = [i for i in ids if assigns[i].kind == "report"]
        evals = [i for i in ids if assigns[i].kind == "evaluation"]
        for p in plans:
            for r in reports:
                edges.append((f"doc:{p}", f"doc:{r}", "plans_reports", "식별자 일치", [f"같은 사업·연차({ynode})의 계획서와 실적보고서"]))
                for ps, s, why in sections.align_context(docs_by_id[p]["sections"], docs_by_id[r]["sections"], text_of, generic_parent=args.generic_parent):
                    edges.append((f"doc:{p}:sec:{ps.path}", f"doc:{r}:sec:{s.path}", "plans_reports", "식별자 일치",
                                  [f"계획 「{ps.title[:60]}」", f"실적 「{s.title[:60]}」", why]))
        for e in evals:
            for t in plans + reports:
                edges.append((f"doc:{e}", f"doc:{t}", "evaluates", "분류", [f"같은 사업·연차({ynode})의 평가 결과"]))
    # 연차 비교 — 다음 연차 계획서의 같은 절
    prog_years = defaultdict(list)
    for ynode in by_year:
        prog, key = ynode.rsplit(":", 1)
        prog_years[(prog, key[0])].append((int(key[1:]), ynode))      # 차수끼리(r)·연도끼리(y)만 잇는다
    for prog, ys in prog_years.items():
        ys.sort()
        for (k1, y1), (k2, y2) in zip(ys, ys[1:]):
            for p1 in (i for i in by_year[y1] if assigns[i].kind == "plan"):
                for p2 in (i for i in by_year[y2] if assigns[i].kind == "plan"):
                    for s, t, why in sections.align_context(docs_by_id[p1]["sections"], docs_by_id[p2]["sections"], text_of, generic_parent=args.generic_parent):
                        edges.append((f"doc:{p1}:sec:{s.path}", f"doc:{p2}:sec:{t.path}", "continues", "식별자 일치",
                                      [f"{y1} 「{s.title[:50]}」", f"{y2} 「{t.title[:50]}」", why]))

    print("== 그래프")
    print("  노드", dict(Counter(n[1] for n in nodes)))
    print("  관계", dict(Counter((e[2], e[3]) for e in edges)))
    review = [d["id"] for d in docs if assigns[d["id"]].status != "auto"]
    print("  검토 대기 문서", review or "없음")
    if not args.apply:
        print("미리 보기입니다 — --apply 로 쓴다")
        return 0
    kg_store.ensure(db)
    with db._conn() as conn:
        for d in docs:
            kg_store.clear_doc(conn, f"doc:{d['id']}")
        for n in nodes:
            kg_store.put_node(conn, *n)
        for e in edges:
            kg_store.put_edge(conn, *e)
    print(f"썼다 — 노드 {len(nodes)} · 관계 {len(edges)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
