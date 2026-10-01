"""사업 중심 그래프 화면 — 온톨로지 v3(ADR-0048 6항). 사업 하나를 연차 열 × (계획·실적·평가·기타) 행으로 펼치고, 관계마다 기준과 근거를 보인다.

옛 /graph(문서 단위 그래프, 요청마다 다시 만듦)와 따로 둔다. 데이터는 kg_nodes·kg_edges(scripts/157)에서 읽는다.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from zzaimy.graph import kg_store

router = APIRouter()

ROWS = [("plan", "계획서"), ("report", "실적보고서"), ("evaluation", "평가 결과")]
KIND_KO = {"contains": "포함", "plans_reports": "계획↔실적", "continues": "연차 이어짐", "evaluates": "평가"}


def program_view(db, program_id: str | None) -> dict:
    kg_store.ensure(db)
    progs = kg_store.nodes(db, "program")
    if not progs:
        return {"programs": [], "program": None}
    edges = kg_store.edges(db)
    n_docs: dict[str, int] = defaultdict(int)
    for e in edges:
        if e["kind"] == "contains" and e["basis"] == "분류" and e["dst"].startswith("doc:"):
            n_docs[e["src"]] += 1
    # 고른 사업이 없으면 문서가 가장 많은 사업(연차 노드 아래 문서도 그 사업 몫)
    for e in edges:
        if e["kind"] == "contains" and e["src"].startswith("program:") and e["dst"].startswith("year:"):
            n_docs[e["src"]] += n_docs.get(e["dst"], 0)
    prog = next((p for p in progs if p["id"] == program_id), None) or max(progs, key=lambda p: n_docs.get(p["id"], 0))
    nodes = {n["id"]: n for n in kg_store.nodes(db)}
    out_of = defaultdict(list)
    for e in edges:
        out_of[e["src"]].append(e)
    years = [nodes[e["dst"]] for e in out_of[prog["id"]] if e["kind"] == "contains" and e["dst"].startswith("year:")]

    def year_key(y: dict) -> tuple:
        k = y["id"].rsplit(":", 1)[1]
        return (0 if k.startswith("r") else 1, int(k[1:]) if k[1:].isdigit() else 0)
    years.sort(key=year_key)
    loose = [nodes[e["dst"]] for e in out_of[prog["id"]] if e["kind"] == "contains" and e["dst"].startswith("doc:")]
    cols = []
    doc_year: dict[str, str] = {}
    for y in years:
        docs = [nodes[e["dst"]] | {"why": e["evidence"]} for e in out_of[y["id"]] if e["dst"] in nodes]
        for d in docs:
            doc_year[d["id"]] = y["label"]
        cells = {k: [d for d in docs if d["props"].get("kind") == k] for k, _ in ROWS}
        cells["other"] = [d for d in docs if d["props"].get("kind") not in dict(ROWS)]
        cols.append({"year": y, "cells": cells})
    prog_docs = set(doc_year) | {d["id"] for d in loose}

    def doc_of(node_id: str) -> str:
        return node_id.split(":sec:")[0]

    def pair(e: dict) -> dict:
        a, b = nodes.get(e["src"], {}), nodes.get(e["dst"], {})
        return {"src": e["src"], "dst": e["dst"], "a": a.get("label", e["src"]), "b": b.get("label", e["dst"]),
                "a_doc": doc_of(e["src"]).split(":")[1], "b_doc": doc_of(e["dst"]).split(":")[1],
                "basis": e["basis"], "why": e["evidence"][-1] if e["evidence"] else ""}
    plans_reports = defaultdict(list)
    continues = []
    evaluates = []
    for e in edges:
        if doc_of(e["src"]) not in prog_docs:
            continue
        if e["kind"] == "plans_reports" and ":sec:" in e["src"]:
            plans_reports[doc_year.get(doc_of(e["src"]), "연차 미상")].append(pair(e))
        elif e["kind"] == "continues":
            continues.append(pair(e))
        elif e["kind"] == "evaluates":
            evaluates.append(pair(e))
    counts = defaultdict(int)
    for e in edges:
        if doc_of(e["src"]) in prog_docs:
            counts[(e["kind"], e["basis"])] += 1
    return {"programs": progs, "program": prog, "cols": cols, "loose": loose, "rows": ROWS,
            "plans_reports": dict(plans_reports), "continues": continues, "evaluates": evaluates,
            "counts": sorted(((KIND_KO.get(k[0], k[0]), k[1], v) for k, v in counts.items()), key=lambda t: -t[2]),
            "n_sections": sum(1 for n in nodes.values() if n["type"] == "section" and doc_of(n["id"]) in prog_docs)}


@router.get("/graph/program", response_class=HTMLResponse)
def graph_program(request: Request, id: str = ""):
    st = request.app.state
    data = program_view(st.db, id or None)
    return st.templates.TemplateResponse(request, "kg_program.html", st.page_ctx(request, data))
