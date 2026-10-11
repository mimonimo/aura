"""사업 중심 그래프 화면 — 온톨로지 v3(ADR-0048 6항). 사업 하나를 연차 열 × (계획·실적·평가·기타) 행으로 펼치고, 관계마다 기준과 근거를 보인다.

옛 /graph(문서 단위 그래프, 요청마다 다시 만듦)와 따로 둔다. 데이터는 kg_nodes·kg_edges(scripts/157)에서 읽는다.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from zzaimy.app.kg_explore import label_of, when_text
from zzaimy.graph import kg_store
from zzaimy.graph.programs import alias_filters, alias_list

router = APIRouter()

ROWS = [("plan", "계획서"), ("report", "실적보고서"), ("evaluation", "평가 결과")]
KIND_KO = {"contains": "포함", "plans_reports": "계획↔실적", "continues": "연차 이어짐", "evaluates": "평가",
           "has_indicator": "성과지표", "measures": "지표 값"}
MEASURE_KO = {"baseline": "기준", "target": "목표", "actual": "실적", "rate": "달성률", "": "값"}


CELL_MAX = 6            # 연차 × 갈래 칸마다 처음 보이는 문서 수 — 나머지는 그 연차 화면(쪽 나눔)에서
YEAR_PAGE = 60          # 연차 화면 한 쪽의 문서 수
PAIRS_MAX = 40          # 연차마다 처음 보이는 계획↔실적 짝 수
CONTINUES_MAX = 60
LOOSE_MAX = 30


def match_degree(why: str) -> str:
    """관계 근거의 내부 점수(「맥락 점수 0.61」「본문 겹침 0.42」)를 「일치 정도 높음·보통·낮음」으로. 점수가 없으면 그대로."""
    m = re.search(r"(?:점수|겹침)\s*([01](?:\.\d+)?)", why or "")
    if not m:
        return why or ""
    v = float(m.group(1))
    degree = "높음" if v >= 0.6 else ("보통" if v >= 0.35 else "낮음")
    head = re.sub(r"[·\s]*(?:맥락\s*)?(?:점수|본문\s*겹침|겹침)\s*[01](?:\.\d+)?", "", why).strip(" ·")
    return (head + " · " if head else "") + f"일치 정도 {degree}"


def indicator_table(inds: list[dict], vis_docs: set | None, limit: int = 40, max_cols: int = 8) -> dict:
    """사업의 성과지표 × 연도 표 — 칸마다 문서 표에서 꺼낸 값 그대로(갈래별, 서로 다른 값은 모두). 계산한 수치는 넣지 않는다.
    이름이 끝 각주 번호만 다른 지표(「양성인원」「양성인원 5)」)는 한 줄로 — 그래프를 다시 짓기 전 노드에도 맞게 여기서 합친다."""
    from zzaimy.graph.indicators import name_key, strip_footnote

    rows, years = [], set()
    by_key: dict[str, dict] = {}
    for n in inds:
        obs = [o for o in (n.get("props") or {}).get("obs", []) if vis_docs is None or o.get("doc_id") in vis_docs]
        if not obs:
            continue
        label = strip_footnote(n["label"])
        key = name_key(label) or n["id"]
        row = by_key.get(key)
        if row is None:
            row = by_key[key] = {"id": n["id"], "label": label, "unit": (n.get("props") or {}).get("unit", ""),
                                 "tags": list((n.get("props") or {}).get("tags", [])), "docs": set(),
                                 "cells": defaultdict(lambda: defaultdict(dict))}
            rows.append(row)
        cells = row["cells"]
        for o in obs:
            col = str(o["year"]) if o.get("year") else (o.get("period") or "기준" if o.get("measure") == "baseline" else (o.get("period") or "시점 미상"))
            years.add(col)
            cells[col][MEASURE_KO.get(o.get("measure") or "", "값")].setdefault(o.get("text") or "", []).append(o.get("doc_id"))
            row["docs"].add(o.get("doc_id"))
        row["unit"] = row["unit"] or (n.get("props") or {}).get("unit", "")
        row["tags"] = row["tags"] + [t for t in (n.get("props") or {}).get("tags", []) if t not in row["tags"]]
    for row in rows:
        row["n_docs"] = len(row.pop("docs"))
    rows.sort(key=lambda r: -r["n_docs"])
    rows = rows[:limit]
    used = {c for r in rows for c in r["cells"]}
    cols = sorted((c for c in used if c.isdigit()), key=int)[-max_cols:]
    rest = sorted(c for c in used if not c.isdigit())
    return {"rows": rows, "cols": [c for c in rest if c == "기준"] + cols + [c for c in rest if c != "기준"][:3], "total": len(inds)}


def _rows(conn, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _node(r: dict) -> dict:
    return {**r, "props": json.loads(r["props"] or "{}") if isinstance(r.get("props"), str) else (r.get("props") or {})}


def _edge(r: dict) -> dict:
    return {**r, "evidence": json.loads(r["evidence"] or "[]") if isinstance(r.get("evidence"), str) else (r.get("evidence") or [])}


def _in(ids: list, size: int = 5000):
    for i in range(0, len(ids), size):
        part = ids[i:i + size]
        yield part, ",".join("?" * len(part))


def _subgraph(db, program_id: str | None, scope: dict | None) -> tuple[dict, list, dict | None]:
    """고른 사업 하나를 그리는 데 필요한 노드·관계만 읽는다.

    그래프 전체(노드 수십만)와 문서 본문까지 읽으면 화면이 수십 초 걸린다. 그래서 사업·연차 뼈대(분류 관계)는 전부, 문서·지표·대응 관계는 고른 사업 것만 읽고, 절 수·관계 수는 SQL 로 센다."""
    from zzaimy.app.access_policy import visible

    with db._conn() as conn:
        base = {r["id"]: _node(r) for r in _rows(conn, "SELECT * FROM kg_nodes WHERE type IN ('program', 'program_group', 'year')")}
        struct = [_edge(r) for r in _rows(conn, "SELECT * FROM kg_edges WHERE (kind = 'contains' AND (src LIKE 'program:%' OR src LIKE 'year:%'"
                                                 " OR src LIKE 'group:%')) OR kind IN ('integrated_into', 'succeeded_by', 'related')")]
        dev = scope is None or scope.get("role") == "dev"
        if dev:
            allowed_docs = None
        else:
            allowed_docs = {int(r["id"]) for r in _rows(conn, "SELECT id, access_level, dept, owner, doc_type, audience FROM documents")
                            if visible(r, **scope)}

        def doc_ok(node_id: str) -> bool:
            if not node_id.startswith("doc:"):
                return True
            tail = node_id.split(":")[1]
            return allowed_docs is None or (tail.isdigit() and int(tail) in allowed_docs)
        # 볼 수 있는 문서를 담은 사업·연차만(숨은 문서의 분류 노드는 드러내지 않는다). 관리자는 전부
        parents = defaultdict(list)
        for e in struct:
            if e["kind"] == "contains" and base.get(e["src"], {}).get("type") in {"program", "year"}:
                parents[e["dst"]].append(e["src"])
        if dev:
            keep = set(base)
        else:
            keep, pending = set(), [e["dst"] for e in struct if e["kind"] == "contains" and e["dst"].startswith("doc:") and doc_ok(e["dst"])]
            while pending:
                for par in parents[pending.pop()]:
                    if par not in keep:
                        keep.add(par)
                        pending.append(par)
            keep |= {k for k, n in base.items() if n["type"] == "program_group"}
        nodes = {k: n for k, n in base.items() if k in keep}
        # 고를 사업 — 지정이 없으면 볼 수 있는 문서가 가장 많은 사업(사업 바로 아래 + 연차 아래)
        n_docs: dict[str, int] = defaultdict(int)
        year_of = {e["dst"]: e["src"] for e in struct if e["kind"] == "contains" and e["src"].startswith("program:") and e["dst"].startswith("year:")}
        for e in struct:
            if e["kind"] == "contains" and e["basis"] == "분류" and e["dst"].startswith("doc:") and doc_ok(e["dst"]):
                n_docs[year_of.get(e["src"], e["src"])] += 1
        progs = [k for k, n in nodes.items() if n["type"] == "program"]
        pid = program_id if program_id in progs else (max(progs, key=lambda k: n_docs.get(k, 0)) if progs and not program_id else None)
        if pid is None:
            return nodes, [], None
        years = [e["dst"] for e in struct if e["src"] == pid and e["dst"].startswith("year:")]
        doc_ids = sorted({e["dst"] for e in struct if e["kind"] == "contains" and e["src"] in {pid, *years}
                          and e["dst"].startswith("doc:") and doc_ok(e["dst"])})
        for part, ph in _in(doc_ids):
            nodes.update({r["id"]: _node(r) for r in _rows(conn, f"SELECT * FROM kg_nodes WHERE id IN ({ph})", part)})
        nums = [int(d.split(":")[1]) for d in doc_ids]
        # 성과지표 — 값을 낸 문서 중 하나라도 볼 수 있을 때만
        ind = [_edge(r) for r in _rows(conn, "SELECT * FROM kg_edges WHERE src = ? AND kind = 'has_indicator'", (pid,))]
        for part, ph in _in([e["dst"] for e in ind]):
            for r in _rows(conn, f"SELECT * FROM kg_nodes WHERE id IN ({ph})", part):
                n = _node(r)
                if dev or any(o.get("doc_id") in allowed_docs for o in n["props"].get("obs", [])):
                    nodes[n["id"]] = n
        # 계획↔실적·연차 이어짐·평가 — 고른 사업 문서(또는 그 절)에서 나가는 것만, 양 끝을 볼 수 있을 때
        pairs, n_sections, counts = [], 0, defaultdict(int)
        for part, ph in _in(nums):
            pairs += [_edge(r) for r in _rows(conn, "SELECT e.* FROM kg_edges e JOIN kg_nodes n ON n.id = e.src"
                                                   f" WHERE n.doc_id IN ({ph}) AND e.kind IN ('plans_reports', 'continues', 'evaluates')", part)]
            n_sections += int(conn.execute(f"SELECT COUNT(*) FROM kg_nodes WHERE type = 'section' AND doc_id IN ({ph})", part).fetchone()[0])
            for r in conn.execute("SELECT e.kind, e.basis, COUNT(*) FROM kg_edges e JOIN kg_nodes n ON n.id = e.src"
                                  f" WHERE n.doc_id IN ({ph}) GROUP BY e.kind, e.basis", part).fetchall():
                counts[(r[0], r[1])] += int(r[2])
        pairs = [e for e in pairs if doc_ok(e["dst"].split(":sec:")[0])]
        ends = sorted({x for e in pairs for x in (e["src"], e["dst"])} - set(nodes))
        for part, ph in _in(ends):
            nodes.update({r["id"]: _node(r) for r in _rows(conn, f"SELECT * FROM kg_nodes WHERE id IN ({ph})", part)})
    edges = [e for e in struct + ind + pairs if e["src"] in nodes and e["dst"] in nodes]
    return nodes, edges, {"counts": counts, "n_sections": n_sections, "allowed_docs": allowed_docs}


def program_view(db, program_id: str | None, scope: dict | None = None) -> dict:
    kg_store.ensure(db)
    nodes, edges, sql = _subgraph(db, program_id, scope)
    allowed_docs = (sql or {}).get("allowed_docs")
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
    loose_all = [nodes[e["dst"]] for e in out_of[prog["id"]] if e["kind"] == "contains" and e["dst"].startswith("doc:")]
    cols = []
    doc_year: dict[str, str] = {}
    for y in years:
        docs = [nodes[e["dst"]] | {"why": e["evidence"]} for e in out_of[y["id"]] if e["dst"] in nodes]
        for d in docs:
            doc_year[d["id"]] = y["label"]
        cells = {k: [d for d in docs if d["props"].get("kind") == k] for k, _ in ROWS}
        cells["other"] = [d for d in docs if d["props"].get("kind") not in dict(ROWS)]
        # 칸마다 앞 CELL_MAX 건만 그린다 — 연차 하나에 문서가 수천 건이면 화면이 멈춘다. 나머지는 그 연차 화면에서 쪽으로 넘긴다
        more = {k: max(len(v) - CELL_MAX, 0) for k, v in cells.items()}
        cols.append({"year": y, "label": _when(y), "n": len(docs), "more": more,
                     "cells": {k: v[:CELL_MAX] for k, v in cells.items()}, "all": docs})
    prog_docs = set(doc_year) | {d["id"] for d in loose_all}

    def doc_of(node_id: str) -> str:
        return node_id.split(":sec:")[0]

    def pair(e: dict) -> dict:
        a, b = nodes.get(e["src"], {}), nodes.get(e["dst"], {})
        return {"src": e["src"], "dst": e["dst"], "a": _sec_label(a, e["src"]), "b": _sec_label(b, e["dst"]),
                "a_doc": doc_of(e["src"]).split(":")[1], "b_doc": doc_of(e["dst"]).split(":")[1],
                "basis": e["basis"], "why": match_degree(e["evidence"][-1] if e["evidence"] else "")}
    plans_reports = defaultdict(list)
    continues = []
    evaluates = []
    seen_pairs: set[tuple] = set()
    for e in edges:
        if doc_of(e["src"]) not in prog_docs or e["kind"] not in ("plans_reports", "continues", "evaluates"):
            continue
        p_ = pair(e)
        sig = (e["kind"], p_["a_doc"], p_["a"], p_["b_doc"], p_["b"])
        if sig in seen_pairs:
            continue                                   # 같은 절 짝이 근거만 달리 여러 번 — 한 줄로
        seen_pairs.add(sig)
        if e["kind"] == "plans_reports" and ":sec:" in e["src"]:
            plans_reports[doc_year.get(doc_of(e["src"]), "연차 미상")].append(p_)
        elif e["kind"] == "continues":
            continues.append(p_)
        elif e["kind"] == "evaluates":
            evaluates.append(p_)
    counts = (sql or {}).get("counts") or defaultdict(int)
    # 사업 체계(외부 확인 장부에서 지은 관계) — 분류, 앵커 편입, 앞뒤 단계
    taxonomy = {"groups": [], "integrated": [], "pred": [], "succ": []}
    name = lambda nid: label_of(nodes[nid]) if nid in nodes else nid
    for e in edges:
        if e["kind"] == "contains" and e["src"].startswith("group:") and e["dst"] == prog["id"]:
            taxonomy["groups"].append(name(e["src"]))
        elif e["kind"] == "integrated_into" and e["src"] == prog["id"]:
            taxonomy["integrated"].append({"to": name(e["dst"]), "id": e["dst"], "why": e["evidence"][0] if e["evidence"] else ""})
        elif e["kind"] == "integrated_into" and e["dst"] == prog["id"]:
            taxonomy.setdefault("members", []).append({"name": name(e["src"]), "id": e["src"], "why": e["evidence"][0] if e["evidence"] else ""})
        elif e["kind"] == "succeeded_by" and e["dst"] == prog["id"]:
            taxonomy["pred"].append({"name": name(e["src"]), "id": e["src"]})
        elif e["kind"] == "succeeded_by" and e["src"] == prog["id"]:
            taxonomy["succ"].append({"name": name(e["dst"]), "id": e["dst"]})
        elif e["kind"] == "related" and prog["id"] in (e["src"], e["dst"]):
            other = e["dst"] if e["src"] == prog["id"] else e["src"]
            taxonomy.setdefault("related", []).append({"name": name(other), "id": other,
                                                       "why": e["evidence"][0] if e["evidence"] else "",
                                                       "out": e["src"] == prog["id"]})
    ledger = (prog.get("props") or {}).get("ledger") or {}
    vis_docs = None if scope is None or scope.get("role") == "dev" else allowed_docs
    indicators = indicator_table([nodes[e["dst"]] for e in out_of[prog["id"]] if e["kind"] == "has_indicator" and e["dst"] in nodes], vis_docs)
    # 별칭 거르기 — 다른 사업의 약칭(그 사업의 이름·id·약칭 목록), 이 사업 정체 약칭의 한 글자 오기(장부에 없으면)는 뺀다
    from zzaimy.app.program_names import load_ledger
    canon, foreign, allowed = alias_filters(prog, [p for p in progs if p["id"] != prog["id"]], load_ledger())
    aliases, more_aliases = alias_list((prog.get("props") or {}).get("names") or [], (prog.get("props") or {}).get("acronyms") or [],
                                       foreign=foreign, canonical=canon, allowed=allowed)
    shown = label_of(prog)
    aliases = [a for a in aliases if a.replace(" ", "") != shown.replace(" ", "")]
    # 위 칩 — 그래프 탐색 왼쪽 목록과 같은 이름·같은 수(kg_explore.programs). 볼 수 있는 사업만
    from zzaimy.app.kg_explore import program_doc_sets, programs as program_list
    visible_ids = {p["id"] for p in progs}
    chips = [p for p in program_list(db, scope) if p["type"] == "program" and set(p["ids"]) & visible_ids]
    n_prog_docs = len(program_doc_sets(db, scope).get(prog["id"], set()))
    return {"indicators": indicators, "programs": progs, "chips": chips, "cell_max": CELL_MAX, "program": prog, "program_label": shown,
            "n_docs": n_prog_docs, "aliases": aliases, "more_aliases": more_aliases,
            "taxonomy": taxonomy, "ledger": ledger, "cols": cols, "rows": ROWS,
            "loose": loose_all[:LOOSE_MAX], "more_loose": max(len(loose_all) - LOOSE_MAX, 0),
            "plans_reports": {y: v[:PAIRS_MAX] for y, v in plans_reports.items()},
            "plans_reports_total": {y: len(v) for y, v in plans_reports.items()},
            "continues": continues[:CONTINUES_MAX], "continues_total": len(continues), "evaluates": evaluates,
            "counts": sorted(((KIND_KO.get(k[0], k[0]), k[1], v) for k, v in counts.items()), key=lambda t: -t[2]),
            "n_sections": (sql or {}).get("n_sections", 0)}


def _when(y: dict) -> str:
    p = y.get("props") or {}
    if p.get("round") or p.get("year"):
        return when_text(p.get("year"), p.get("round"))
    return " ".join(str(y.get("label") or "").split(" ")[-2:])


def _sec_label(n: dict, fallback: str) -> str:
    return label_of(n) if n else fallback


def year_page(data: dict, year_id: str, page: int = 1) -> dict:
    """연차 하나의 문서 전부를 쪽으로 — 사업 화면 칸에는 앞 몇 건만 그리므로 나머지는 여기서 본다."""
    col = next((c for c in data.get("cols") or [] if c["year"]["id"] == year_id), None)
    if col is None:
        raise HTTPException(404, "연차를 찾을 수 없습니다")
    kinds = dict(ROWS) | {"other": "기타"}
    order = {k: i for i, k in enumerate(kinds)}
    docs = sorted(col["all"], key=lambda d: (order.get(d["props"].get("kind") if d["props"].get("kind") in kinds else "other", 9),
                                             str(d.get("label") or "")))
    pages = max((len(docs) + YEAR_PAGE - 1) // YEAR_PAGE, 1)
    page = min(max(page, 1), pages)
    part = docs[(page - 1) * YEAR_PAGE: page * YEAR_PAGE]
    for d in part:
        k = d["props"].get("kind")
        d["kind_ko"] = kinds.get(k if k in kinds else "other")
    return {"label": col["label"], "id": year_id, "docs": part, "page": page, "pages": pages, "total": len(docs)}


@router.get("/graph/program", response_class=HTMLResponse)
def graph_program(request: Request, id: str = "", year: str = "", page: int = 1):
    st = request.app.state
    scope = {"dept": getattr(request.state, "dept", "") or None,
             "user": request.state.user, "role": request.state.role}
    data = program_view(st.db, id or None, scope=scope)
    if year and data.get("program"):
        data["year_view"] = year_page(data, year, page)
    return st.templates.TemplateResponse(request, "kg_program.html", st.page_ctx(request, data))
