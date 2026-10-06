"""지식 그래프 탐색 화면 — kg_nodes·kg_edges(scripts/157)를 노드 하나씩 펼쳐 본다. 그리기는 Cytoscape.js(MIT, static 에 동봉).

그래프 전체(노드 42만)를 내려보내지 않는다. 고른 노드의 이웃만 관계 종류별 상한으로 잘라 보내고, 화면이 펼칠 때마다 더 받는다.
문서·절·성과지표 노드는 열람 권한(access_policy.visible)으로 거른다 — 볼 수 없는 문서의 노드는 이웃 목록에도 수에도 넣지 않는다.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from zzaimy.graph import kg_store

router = APIRouter()

TYPE_KO = {"program_group": "사업 묶음", "program": "사업", "year": "연차", "doc": "문서", "section": "절",
           "unit": "단위과제", "indicator": "성과지표"}
# 관계 이름은 나가는 쪽(out)·들어오는 쪽(in)에서 읽는 말이 다르다
KIND_KO = {
    "contains": ("포함", "소속"), "instance_of": ("같은 단위과제", "이 단위과제의 절"),
    "continues": ("다음 연차로 이어짐", "앞 연차에서 이어짐"), "plans_reports": ("계획 → 실적", "실적 ← 계획"),
    "evaluates": ("평가함", "평가받음"), "has_indicator": ("성과지표", "이 지표를 둔 사업"),
    "measures": ("지표 값", "값을 낸 문서"), "related": ("연관 사업", "연관 사업"),
    "integrated_into": ("편입됨", "편입받음"), "succeeded_by": ("후속 사업", "전신 사업"),
}
SEARCH_TYPES = ("program_group", "program", "year", "unit", "indicator", "doc")
PER_GROUP = 12          # 관계 종류·방향·상대 종류마다 한 번에 보내는 이웃 수 — 많으면 한 화면에서 읽히지 않는다(10/6 크롬 확인)
_cache: dict = {}


def _scope(request: Request) -> dict:
    return {"dept": getattr(request.state, "dept", "") or None, "user": request.state.user, "role": request.state.role}


def _visible_docs(db, doc_ids: set[int], scope: dict) -> set[int]:
    """그 중 이 사용자가 볼 수 있는 문서 id."""
    if not doc_ids or scope.get("role") == "dev":
        return set(doc_ids)
    from zzaimy.app.access_policy import visible

    ids, out = sorted(doc_ids), set()
    with db._conn() as conn:
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            rows = conn.execute(f"SELECT * FROM documents WHERE id IN ({','.join('?' * len(part))})", part).fetchall()
            out |= {int(dict(r)["id"]) for r in rows if visible(dict(r), **scope)}
    return out


def _props(raw) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {}


def _node_docs(n: dict) -> set[int]:
    """노드가 기대는 문서 — 문서·절은 자기 문서, 성과지표는 값을 낸 문서들. 없으면 빈 집합(사업·연차·단위과제)."""
    if n.get("doc_id") is not None:
        return {int(n["doc_id"])}
    if n["type"] == "indicator":
        return {int(o["doc_id"]) for o in n["props"].get("obs", []) if o.get("doc_id") is not None}
    return set()


def _summary(n: dict) -> dict:
    p = n["props"]
    keep = {k: p[k] for k in ("kind_label", "year", "round", "n_docs", "unit", "level", "status") if p.get(k) not in (None, "", [])}
    if n["type"] == "program":
        led = p.get("ledger") or {}
        keep.update({k: led[k] for k in ("period", "ministry", "agency") if led.get(k)})
    return {"id": n["id"], "type": n["type"], "type_ko": TYPE_KO.get(n["type"], n["type"]), "label": n["label"],
            "doc_id": n.get("doc_id"), "info": keep}


def _load_nodes(db, ids: list[str]) -> dict[str, dict]:
    out = {}
    with db._conn() as conn:
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            for r in conn.execute(f"SELECT id, type, label, props, doc_id FROM kg_nodes WHERE id IN ({','.join('?' * len(part))})", part):
                r = dict(r)
                out[r["id"]] = r | {"props": _props(r["props"])}
    return out


def _allowed(db, nodes: dict[str, dict], scope: dict) -> set[str]:
    need = {n["id"]: _node_docs(n) for n in nodes.values()}
    vis = _visible_docs(db, set().union(*need.values()) if need else set(), scope)
    return {k for k, docs in need.items() if not docs or docs & vis}


def _group_label(rel: str, typ: str) -> str:
    """「포함 · 연차」처럼 관계와 상대 종류 — 관계 이름이 이미 종류를 말하면(성과지표·연관 사업) 덧붙이지 않는다."""
    return rel if typ in rel else f"{rel} · {typ}"


def neighbors(db, node_id: str, scope: dict, per_group: int = PER_GROUP, offset: dict | None = None) -> dict:
    kg_store.ensure(db)
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT src, dst, kind, basis, evidence, weight FROM kg_edges WHERE src = ?"
            " UNION ALL SELECT src, dst, kind, basis, evidence, weight FROM kg_edges WHERE dst = ?", (node_id, node_id))]
    center = _load_nodes(db, [node_id]).get(node_id)
    if center is None or node_id not in _allowed(db, {node_id: center}, scope):
        raise HTTPException(404, "노드를 찾을 수 없습니다")
    others = _load_nodes(db, sorted({r["dst"] if r["src"] == node_id else r["src"] for r in rows}))
    ok = _allowed(db, others, scope)
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        out = r["src"] == node_id
        other = r["dst"] if out else r["src"]
        if other not in ok or other == node_id:
            continue
        groups[(r["kind"], "out" if out else "in", others[other]["type"])].append((r, others[other]))
    res_nodes, res_edges, res_groups = {}, [], []
    rank = {t: i for i, t in enumerate(TYPE_KO)}
    for (kind, d, typ), items in sorted(groups.items(), key=lambda kv: (list(KIND_KO).index(kv[0][0]) if kv[0][0] in KIND_KO else 99,
                                                                        kv[0][1], rank.get(kv[0][2], 99))):
        items.sort(key=lambda t: (-float(t[0]["weight"] or 0), t[1]["label"]))
        key = f"{kind}:{d}:{typ}"
        start = int((offset or {}).get(key, 0))
        page = items[start:start + per_group]
        for r, n in page:
            res_nodes[n["id"]] = _summary(n)
            res_edges.append({"src": r["src"], "dst": r["dst"], "kind": kind, "basis": r["basis"],
                              "evidence": _props(r["evidence"]) if isinstance(r["evidence"], str) else r["evidence"],
                              "weight": r["weight"]})
        names = KIND_KO.get(kind, (kind, kind))
        res_groups.append({"key": key, "kind": kind, "dir": d, "type": typ,
                           "label": _group_label(names[0] if d == "out" else names[1], TYPE_KO.get(typ, typ)),
                           "total": len(items), "shown": start + len(page)})
    return {"center": _summary(center), "nodes": list(res_nodes.values()), "edges": res_edges, "groups": res_groups}


def search(db, q: str, scope: dict, limit: int = 40) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    kg_store.ensure(db)
    ph = ",".join("?" * len(SEARCH_TYPES))
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, type, label, props, doc_id FROM kg_nodes WHERE type IN ({ph}) AND label LIKE ? LIMIT 400",
            (*SEARCH_TYPES, f"%{q}%"))]
    nodes = {r["id"]: r | {"props": _props(r["props"])} for r in rows}
    ok = _allowed(db, nodes, scope)
    order = {t: i for i, t in enumerate(SEARCH_TYPES)}
    hits = sorted((n for k, n in nodes.items() if k in ok), key=lambda n: (order.get(n["type"], 9), len(n["label"])))
    return [_summary(n) for n in hits[:limit]]


def programs(db) -> list[dict]:
    """사업 목록과 문서 수(사업 바로 아래 + 연차 아래) — 5분 캐시."""
    hit = _cache.get("programs")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    kg_store.ensure(db)
    with db._conn() as conn:
        progs = [dict(r) for r in conn.execute("SELECT id, type, label FROM kg_nodes WHERE type IN ('program', 'program_group')")]
        year_of = {r[0]: r[1] for r in conn.execute(
            "SELECT dst, src FROM kg_edges WHERE kind = 'contains' AND src LIKE 'program:%' AND dst LIKE 'year:%'")}
        n_docs: dict[str, int] = defaultdict(int)
        for r in conn.execute("SELECT src, COUNT(*) FROM kg_edges WHERE kind = 'contains' AND dst LIKE 'doc:%' GROUP BY src"):
            n_docs[year_of.get(r[0], r[0])] += int(r[1])
        # 사업 묶음은 묶인 사업들의 문서 수
        for r in conn.execute("SELECT src, dst FROM kg_edges WHERE kind = 'contains' AND src LIKE 'group:%' AND dst LIKE 'program:%'"):
            n_docs[r[0]] += n_docs.get(r[1], 0)
    out = sorted(({"id": p["id"], "type": p["type"], "type_ko": TYPE_KO.get(p["type"], p["type"]), "label": p["label"],
                   "n_docs": n_docs.get(p["id"], 0)} for p in progs), key=lambda p: (p["type"] != "program_group", -p["n_docs"]))
    _cache["programs"] = (time.time(), out)
    return out


def schema(db) -> dict:
    """온톨로지 구조도 — 노드 종류별 수와 (종류 → 종류, 관계, 기준)별 관계 수. 그래프 재구축 주기보다 짧게 10분 캐시."""
    hit = _cache.get("schema")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    kg_store.ensure(db)
    with db._conn() as conn:
        types = {r[0]: int(r[1]) for r in conn.execute("SELECT type, COUNT(*) FROM kg_nodes GROUP BY type")}
        rels = [{"src": r[0], "dst": r[1], "kind": r[2], "basis": r[3], "n": int(r[4])} for r in conn.execute(
            "SELECT a.type, b.type, e.kind, e.basis, COUNT(*) FROM kg_edges e"
            " JOIN kg_nodes a ON a.id = e.src JOIN kg_nodes b ON b.id = e.dst GROUP BY a.type, b.type, e.kind, e.basis")]
    for r in rels:
        r["label"] = KIND_KO.get(r["kind"], (r["kind"],))[0]
    out = {"types": [{"type": t, "type_ko": TYPE_KO.get(t, t), "n": n} for t, n in types.items()], "rels": rels}
    _cache["schema"] = (time.time(), out)
    return out


@router.get("/graph/explore/schema")
def explore_schema(request: Request):
    return JSONResponse(schema(request.app.state.db))


@router.get("/graph/explore", response_class=HTMLResponse)
def explore_page(request: Request, id: str = "", q: str = ""):
    st = request.app.state
    return st.templates.TemplateResponse(request, "kg_explore.html", st.page_ctx(request, {
        "focus": id, "q": q, "programs": programs(st.db), "type_ko": TYPE_KO}))


@router.get("/graph/explore/neighbors")
def explore_neighbors(request: Request, id: str, more: str = ""):
    offset = {}
    if more:                                    # 「더 보기」 — 그 관계 묶음의 다음 쪽
        key, _, start = more.rpartition("@")
        offset = {key: int(start) if start.isdigit() else 0}
    return JSONResponse(neighbors(request.app.state.db, id, _scope(request), offset=offset))


@router.get("/graph/explore/search")
def explore_search(request: Request, q: str = ""):
    return JSONResponse(search(request.app.state.db, q, _scope(request)))
