"""사업 중심 그래프 화면 — 온톨로지 v3(ADR-0048 6항). 사업 하나를 연차 열 × (계획·실적·평가·기타) 행으로 펼치고, 관계마다 기준과 근거를 보인다.

옛 /graph(문서 단위 그래프, 요청마다 다시 만듦)와 따로 둔다. 데이터는 kg_nodes·kg_edges(scripts/157)에서 읽는다.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from zzaimy.graph import kg_store

router = APIRouter()

ROWS = [("plan", "계획서"), ("report", "실적보고서"), ("evaluation", "평가 결과")]
KIND_KO = {"contains": "포함", "plans_reports": "계획↔실적", "continues": "연차 이어짐", "evaluates": "평가",
           "has_indicator": "성과지표", "measures": "지표 값"}
MEASURE_KO = {"baseline": "기준", "target": "목표", "actual": "실적", "rate": "달성률", "": "값"}


def indicator_table(inds: list[dict], vis_docs: set | None, limit: int = 40, max_cols: int = 8) -> dict:
    """사업의 성과지표 × 연도 표 — 칸마다 문서 표에서 꺼낸 값 그대로(갈래별, 서로 다른 값은 모두). 계산한 수치는 넣지 않는다."""
    rows, years = [], set()
    for n in inds:
        obs = [o for o in (n.get("props") or {}).get("obs", []) if vis_docs is None or o.get("doc_id") in vis_docs]
        if not obs:
            continue
        cells: dict[str, dict[str, dict[str, list]]] = defaultdict(lambda: defaultdict(dict))
        for o in obs:
            col = str(o["year"]) if o.get("year") else (o.get("period") or "기준" if o.get("measure") == "baseline" else (o.get("period") or "시점 미상"))
            years.add(col)
            cells[col][MEASURE_KO.get(o.get("measure") or "", "값")].setdefault(o.get("text") or "", []).append(o.get("doc_id"))
        rows.append({"id": n["id"], "label": n["label"], "unit": (n.get("props") or {}).get("unit", ""),
                     "tags": (n.get("props") or {}).get("tags", []), "n_docs": len({o.get("doc_id") for o in obs}), "cells": cells})
    rows.sort(key=lambda r: -r["n_docs"])
    rows = rows[:limit]
    used = {c for r in rows for c in r["cells"]}
    cols = sorted((c for c in used if c.isdigit()), key=int)[-max_cols:]
    rest = sorted(c for c in used if not c.isdigit())
    return {"rows": rows, "cols": [c for c in rest if c == "기준"] + cols + [c for c in rest if c != "기준"][:3], "total": len(inds)}


def program_view(db, program_id: str | None, scope: dict | None = None) -> dict:
    kg_store.ensure(db)
    nodes = {n["id"]: n for n in kg_store.nodes(db)}
    edges = kg_store.edges(db)
    if scope is not None:
        from zzaimy.app.access_policy import visible

        allowed_docs = {d["id"] for d in db.list_documents() if visible(d, **scope)}
        allowed = {key for key, node in nodes.items() if node.get("doc_id") in allowed_docs}
        # Keep only the classification ancestors of visible documents, never hidden siblings.
        parents = defaultdict(list)
        for edge in edges:
            parent = nodes.get(edge["src"], {})
            if edge["kind"] == "contains" and parent.get("type") in {"program", "year", "group"}:
                parents[edge["dst"]].append(edge["src"])
        pending = list(allowed)
        while pending:
            for parent in parents[pending.pop()]:
                if parent not in allowed:
                    allowed.add(parent)
                    pending.append(parent)
        # 성과지표 노드는 그 값을 낸 문서 중 하나라도 볼 수 있을 때만(값도 볼 수 있는 문서 것만 — indicator_table)
        allowed.update(k for k, n in nodes.items() if n["type"] == "indicator"
                       and any(o.get("doc_id") in allowed_docs for o in (n.get("props") or {}).get("obs", [])))
        if scope.get("role") == "dev":
            allowed.update(k for k, n in nodes.items() if n["type"] in {"program", "year", "group"})
        nodes = {k: n for k, n in nodes.items() if k in allowed}
    edges = [e for e in edges if e["src"] in nodes and e["dst"] in nodes]
    progs = [n for n in nodes.values() if n["type"] == "program"]
    if program_id and not any(p["id"] == program_id for p in progs):
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    if not progs:
        return {"programs": [], "program": None}
    n_docs: dict[str, int] = defaultdict(int)
    for e in edges:
        if e["kind"] == "contains" and e["basis"] == "분류" and e["dst"].startswith("doc:"):
            n_docs[e["src"]] += 1
    # 고른 사업이 없으면 문서가 가장 많은 사업(연차 노드 아래 문서도 그 사업 몫)
    for e in edges:
        if e["kind"] == "contains" and e["src"].startswith("program:") and e["dst"].startswith("year:"):
            n_docs[e["src"]] += n_docs.get(e["dst"], 0)
    prog = next((p for p in progs if p["id"] == program_id), None) or max(progs, key=lambda p: n_docs.get(p["id"], 0))
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
    # 사업 체계(외부 확인 장부에서 지은 관계) — 분류, 앵커 편입, 앞뒤 단계
    taxonomy = {"groups": [], "integrated": [], "pred": [], "succ": []}
    for e in edges:
        if e["kind"] == "contains" and e["src"].startswith("group:") and e["dst"] == prog["id"]:
            taxonomy["groups"].append(nodes.get(e["src"], {}).get("label", e["src"]))
        elif e["kind"] == "integrated_into" and e["src"] == prog["id"]:
            taxonomy["integrated"].append({"to": nodes.get(e["dst"], {}).get("label", e["dst"]), "id": e["dst"], "why": e["evidence"][0] if e["evidence"] else ""})
        elif e["kind"] == "integrated_into" and e["dst"] == prog["id"]:
            taxonomy.setdefault("members", []).append({"name": nodes.get(e["src"], {}).get("label", e["src"]), "id": e["src"], "why": e["evidence"][0] if e["evidence"] else ""})
        elif e["kind"] == "succeeded_by" and e["dst"] == prog["id"]:
            taxonomy["pred"].append({"name": nodes.get(e["src"], {}).get("label", e["src"]), "id": e["src"]})
        elif e["kind"] == "succeeded_by" and e["src"] == prog["id"]:
            taxonomy["succ"].append({"name": nodes.get(e["dst"], {}).get("label", e["dst"]), "id": e["dst"]})
        elif e["kind"] == "related" and prog["id"] in (e["src"], e["dst"]):
            other = e["dst"] if e["src"] == prog["id"] else e["src"]
            taxonomy.setdefault("related", []).append({"name": nodes.get(other, {}).get("label", other), "id": other,
                                                       "why": e["evidence"][0] if e["evidence"] else "",
                                                       "out": e["src"] == prog["id"]})
    ledger = (prog.get("props") or {}).get("ledger") or {}
    vis_docs = None if scope is None or scope.get("role") == "dev" else allowed_docs
    indicators = indicator_table([nodes[e["dst"]] for e in out_of[prog["id"]] if e["kind"] == "has_indicator" and e["dst"] in nodes], vis_docs)
    return {"indicators": indicators, "programs": progs, "program": prog, "taxonomy": taxonomy, "ledger": ledger, "cols": cols, "loose": loose, "rows": ROWS,
            "plans_reports": dict(plans_reports), "continues": continues, "evaluates": evaluates,
            "counts": sorted(((KIND_KO.get(k[0], k[0]), k[1], v) for k, v in counts.items()), key=lambda t: -t[2]),
            "n_sections": sum(1 for n in nodes.values() if n["type"] == "section" and doc_of(n["id"]) in prog_docs)}


@router.get("/graph/program", response_class=HTMLResponse)
def graph_program(request: Request, id: str = ""):
    st = request.app.state
    scope = {"dept": getattr(request.state, "dept", "") or None,
             "user": request.state.user, "role": request.state.role}
    data = program_view(st.db, id or None, scope=scope)
    return st.templates.TemplateResponse(request, "kg_program.html", st.page_ctx(request, data))
