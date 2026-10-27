"""지식 그래프 탐색 화면 — kg_nodes·kg_edges(scripts/157)를 노드 하나씩 펼쳐 본다. 그리기는 Cytoscape.js(MIT, static 에 동봉).

그래프 전체(노드 42만)를 내려보내지 않는다. 고른 노드의 이웃만 관계 종류별 상한으로 잘라 보내고, 화면이 펼칠 때마다 더 받는다.
문서·절·성과지표 노드는 열람 권한(access_policy.visible)으로 거른다 — 볼 수 없는 문서의 노드는 이웃 목록에도 수에도 넣지 않는다.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from zzaimy.graph import kg_store

router = APIRouter()

TYPE_KO = {"program_group": "사업 묶음", "program": "사업", "org": "기관", "year": "연차", "doc": "문서", "section": "절",
           "unit": "단위과제", "indicator": "성과지표"}
# 관계 이름은 나가는 쪽(out)·들어오는 쪽(in)에서 읽는 말이 다르다
KIND_KO = {
    "contains": ("포함", "소속"), "instance_of": ("같은 단위과제", "이 단위과제의 절"),
    "continues": ("다음 연차로 이어짐", "앞 연차에서 이어짐"), "plans_reports": ("계획 → 실적", "실적 ← 계획"),
    "evaluates": ("평가함", "평가받음"), "has_indicator": ("성과지표", "이 지표를 둔 사업"),
    "measures": ("지표 값", "값을 낸 문서"), "related": ("연관 사업", "연관 사업"),
    "integrated_into": ("편입됨", "편입받음"), "succeeded_by": ("후속 사업", "전신 사업"),
    "supervised_by": ("주관 부처", "주관하는 사업"), "managed_by": ("전담기관", "관리하는 사업"),
}
SEARCH_TYPES = ("program_group", "program", "org", "year", "unit", "indicator", "doc")
# 온톨로지 보기의 종류 설명 — 무엇을 나타내고 어디서 오는가(scripts/157)
TYPE_DEF = {
    "program_group": "사업 체계의 큰 갈래(일반재정지원·RISE/앵커·특수목적·타 부처). 외부 확인 장부(kg_external.json)에서 온다",
    "org": "사업을 주관하는 부처·지자체와 관리하는 전담기관. 외부 확인 장부의 값과 출처에서 온다",
    "program": "재정지원 사업 하나. 장부의 정식 이름·약칭·기간·출처와 문서 분류에서 모은 별칭을 가진다",
    "year": "사업의 연차(N차년도·연도). 문서 제목·본문의 연차 표기와 장부 기간으로 정한다",
    "doc": "문서함의 사업 문서 한 건. 갈래(계획서·실적보고서·평가 결과 등)와 연도·연차를 속성으로 가진다",
    "section": "문서의 절(목차 항목). 본문 조각과 이어져 검색 근거가 된다",
    "unit": "여러 문서에 같은 이름으로 나오는 단위과제·프로그램(식별자 일치로 묶음)",
    "indicator": "성과지표. 문서 표에서 기준·목표·실적·달성률 값을 그대로 꺼내 문서·연도별로 둔다",
}
# 노드 속성 이름 — 화면에 보일 말
PROP_KO = {"names": "이름들", "acronyms": "약칭", "ledger": "장부(기간·출처)", "round": "차년도", "year": "연도", "kind": "갈래",
           "kind_label": "갈래 이름", "share": "분류 확신도", "status": "검토 상태", "evidence": "분류 근거", "level": "목차 수준",
           "seq": "순서", "chunks": "본문 조각", "key": "식별 키", "n_docs": "문서 수", "n_sections": "절 수", "unit": "단위",
           "tags": "꼬리표", "obs": "관측 값(문서·연도별)", "sources": "출처", "display": "표시 이름"}
# 외부 확인 장부(사업 체계·부처·전담기관)에서 온 관계 — 근거 문구가 문서에서 오지 않아 누구에게나 보인다
_PUBLIC_EVIDENCE = {"integrated_into", "succeeded_by", "supervised_by", "managed_by"}
PER_GROUP = 12


def _public_evidence(kind: str, src: str) -> bool:
    """근거 문구가 외부 확인 장부에서 온 관계인가 — 사업 묶음 → 사업 분류도 장부 갈래다. 나머지(연관 사업·연차 분류 등)는
    문서 번호·제목·건수를 근거로 적으므로 관리자에게만 보인다."""
    return kind in _PUBLIC_EVIDENCE or (kind == "contains" and str(src).startswith("group:"))          # 관계 종류·방향·상대 종류마다 한 번에 보내는 이웃 수 — 많으면 한 화면에서 읽히지 않는다
_cache: dict = {}


def _fresh(db) -> None:
    """그래프가 다시 지어졌으면 캐시를 비운다(시간 만료는 안전장치)."""
    v = kg_store.version(db)
    if _cache.get("_ver") != v:
        _cache.clear()
        _cache["_ver"] = v


def _kind_ko() -> dict:
    from zzaimy.app.doc_routing import KINDS
    return {**KINDS, "report": "실적보고서", "evaluation": "평가 결과", "criteria": "평가 기준"}


_KIND_KO = _kind_ko()      # 그래프를 다시 짓기 전 옛 노드에 남은 영어 갈래 이름(notice·table…)도 우리말로


def _scope(request: Request) -> dict:
    from zzaimy.app.access_policy import request_viewer
    return request_viewer(request)


def _visible_docs(db, doc_ids: set[int], scope: dict | None) -> set[int]:
    """그 중 문서함에 지금 있고(지운 문서의 그래프 노드는 빼고) 이 사용자가 볼 수 있는 문서 id. scope 가 없거나 개발자면 있는 것 전부."""
    if not doc_ids:
        return set()
    from zzaimy.app.access_policy import visible

    every = scope is None or scope.get("role") == "dev"
    ids, out = sorted(doc_ids), set()
    with db._conn() as conn:
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            if every:
                out |= {int(r[0]) for r in conn.execute(f"SELECT id FROM documents WHERE id IN ({','.join('?' * len(part))})", part)}
                continue
            rows = conn.execute("SELECT id, access_level, dept, doc_type, audience, owner FROM documents"      # 판정에 쓰는 칸만(본문 빼고)
                                f" WHERE id IN ({','.join('?' * len(part))})", part).fetchall()
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


# 분류 상태(graph.programs.Assignment.status) — 화면에는 우리말로
STATUS_KO = {"auto": "자동 분류", "review": "검토 필요", "folder": "폴더로 분류", "period": "사업 기간으로 분류",
             "agent": "에이전트 검토"}


def when_text(year, rnd) -> str:
    """연차 표시 하나 — 「3차년도 (2025)」「2025년」「2차년도」, 둘 다 없으면 「연차 미상」."""
    if rnd and year:
        return f"{rnd}차년도 ({year})"
    if rnd:
        return f"{rnd}차년도"
    return f"{year}년" if year else "연차 미상"


def label_of(n: dict) -> str:
    """보일 이름 — 사업은 공용 이름 규칙(program_names.display_name)과 앞 잘림 다듬기, 묶음은 「(묶음)」을 붙여 같은 이름의
    사업과 가른다. 절은 겹친 로마 숫자·붙은 머리말을 고치고, 성과지표는 끝 각주 번호를 뗀다."""
    from zzaimy.app.program_names import display_name
    from zzaimy.graph.indicators import strip_footnote
    from zzaimy.graph.programs import clean_label
    from zzaimy.graph.sections import clean_heading

    label, typ = str(n.get("label") or ""), n.get("type")
    if typ in ("program", "program_group"):
        name = display_name(clean_label(label)) or label
        return f"{name} (묶음)" if typ == "program_group" else name
    if typ == "section":
        return clean_heading(clean_label(label))
    if typ == "unit":
        return clean_label(label)
    if typ == "indicator":
        return strip_footnote(label)
    return label


def _summary(n: dict, scope: dict | None = None) -> dict:
    """화면에 보낼 노드 요약. 노드 속성의 문서 수(n_docs)는 그래프 전체(권한 밖 문서 포함)를 센 값이라 관리자에게만 싣는다."""
    p = n["props"]
    keep = {k: p[k] for k in ("kind_label", "n_docs", "unit", "level") if p.get(k) not in (None, "", [])}
    if not _dev(scope):
        keep.pop("n_docs", None)
    if keep.get("kind_label"):
        keep["kind_label"] = _KIND_KO.get(keep["kind_label"], keep["kind_label"])
    if n["type"] in ("doc", "year") and (p.get("year") or p.get("round")):
        keep = {"when": when_text(p.get("year"), p.get("round")), **keep}
    if p.get("status"):
        keep["status"] = STATUS_KO.get(p["status"], "기타")
    if n["type"] == "program":
        led = p.get("ledger") or {}
        keep.update({k: led[k] for k in ("period", "ministry", "agency") if led.get(k)})
    return {"id": n["id"], "type": n["type"], "type_ko": TYPE_KO.get(n["type"], n["type"]), "label": label_of(n),
            "short": _short(n), "doc_id": n.get("doc_id"), "info": keep}


def _short(n: dict) -> str:
    """그림에 쓸 짧은 이름 — 연차는 「N차년도 (YYYY)」, 문서는 확장자 뺀 이름. 긴 사업 이름을 연차마다 되풀이하지 않는다."""
    p, label = n["props"], label_of(n)
    if n["type"] == "year" and p.get("round"):
        return when_text(p.get("year"), p.get("round"))
    if n["type"] in ("doc", "section"):
        return re.sub(r"\.(hwpx?|pdf|docx?|xlsx?|pptx?|txt)$", "", label.replace("_", " "), flags=re.I)
    return label


def _load_nodes(db, ids: list[str]) -> dict[str, dict]:
    out = {}
    with db._conn() as conn:
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            for r in conn.execute(f"SELECT id, type, label, props, doc_id FROM kg_nodes WHERE id IN ({','.join('?' * len(part))})", part):
                r = dict(r)
                out[r["id"]] = r | {"props": _props(r["props"])}
    return out


def _unit_docs(db, unit_ids: list[str]) -> dict[str, set[int]]:
    """단위과제 노드 → 그 이름을 낸 문서(instance_of 로 이어진 문서·절의 문서). 단위과제 이름은 문서의 절 제목에서 온다."""
    out: dict[str, set[int]] = {u: set() for u in unit_ids}
    with db._conn() as conn:
        for i in range(0, len(unit_ids), 500):
            part = unit_ids[i:i + 500]
            for src, dst in conn.execute("SELECT src, dst FROM kg_edges WHERE kind = 'instance_of'"
                                         f" AND dst IN ({','.join('?' * len(part))}) AND src LIKE 'doc:%'", part):
                tail = str(src).split(":")[1]
                if tail.isdigit():
                    out[dst].add(int(tail))
    return out


def _dev(scope: dict | None) -> bool:
    return scope is None or scope.get("role") == "dev"


def _allowed(db, nodes: dict[str, dict], scope: dict) -> set[str]:
    """볼 수 있는 노드 — 문서에 기대는 노드(문서·절·성과지표·단위과제)는 그 문서 가운데 하나라도 볼 수 있을 때만.
    단위과제 이름도 문서의 절 제목에서 오므로 권한 밖 문서에만 나오는 단위과제는 이름조차 보이지 않는다."""
    need = {n["id"]: _node_docs(n) for n in nodes.values()}
    if not _dev(scope):
        units = [k for k, n in nodes.items() if n.get("type") == "unit"]
        if units:
            got = _unit_docs(db, units)
            for u in units:
                need[u] = got[u] or {-1}            # 이어진 문서가 없으면 관리자만
    vis = _visible_docs(db, {d for v in need.values() for d in v if d >= 0}, scope)
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
    from zzaimy.graph.sections import placeholder_title
    # 양식 빈칸 자리표시 절(「0000년 …」「ㅇㅇㅇ대학교」) — 그래프를 다시 짓기 전 노드도 보이지 않게(157 은 이제 만들지 않는다)
    ok = {k for k in ok if not (others[k]["type"] == "section" and placeholder_title(others[k]["label"]))}
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
            res_nodes[n["id"]] = _summary(n, scope)
            ev = _props(r["evidence"]) if isinstance(r["evidence"], str) else r["evidence"]
            if not _dev(scope) and not (_node_docs(n) or _node_docs(center) or _public_evidence(kind, r["src"])):
                ev = []                             # 사업·연차·단위과제 사이 관계의 근거 문구는 문서 번호·건수를 담는다 — 관리자만
            res_edges.append({"src": r["src"], "dst": r["dst"], "kind": kind, "basis": r["basis"],
                              "evidence": ev, "weight": r["weight"]})
        names = KIND_KO.get(kind, (kind, kind))
        res_groups.append({"key": key, "kind": kind, "dir": d, "type": typ,
                           "label": _group_label(names[0] if d == "out" else names[1], TYPE_KO.get(typ, typ)),
                           "total": len(items), "shown": start + len(page)})
    return {"center": _summary(center, scope), "nodes": list(res_nodes.values()), "edges": res_edges, "groups": res_groups}


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
    # 보일 이름 규칙(label_of)을 거친 이름으로 — 문장 조각(사업·단위과제)은 빼고, 다듬은 뒤 같은 이름·같은 종류는 한 번만
    from zzaimy.graph.programs import fragment_name
    out, seen = [], set()
    for n in hits:
        sm = _summary(n, scope)
        if n["type"] in ("program", "unit") and fragment_name(sm["label"]):
            continue
        key = (n["type"], re.sub(r"[\s.·ㆍ\-_()]+", "", sm["label"]).upper())
        if n["type"] != "doc" and key in seen:
            continue
        seen.add(key)
        out.append(sm)
        if len(out) >= limit:
            break
    return out


def _scope_key(scope: dict | None) -> tuple:
    if scope is None or scope.get("role") == "dev":
        return ("all",)
    return (scope.get("role"), scope.get("user"), scope.get("dept"), tuple(scope.get("also") or ()), tuple(scope.get("lent") or ()))


def program_doc_sets(db, scope: dict | None = None) -> dict[str, set[int]]:
    """사업·사업 묶음마다 문서 집합 — 그래프 화면의 「문서 n건」은 모두 이것을 센다.

    정의: 그래프가 그 사업(바로 아래 + 연차 아래, 묶음은 묶인 사업들의 합집합)으로 분류한 문서 가운데 문서함에 지금 있고
    보는 사람이 열람할 수 있는 등록 문서. 원본 파일 수·조각 수가 아니다. 보는 사람 범위마다 5분 캐시."""
    _fresh(db)
    key = ("docsets", str(getattr(db, "path", id(db)))) + _scope_key(scope)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    kg_store.ensure(db)
    with db._conn() as conn:
        year_of = {r[0]: r[1] for r in conn.execute(
            "SELECT dst, src FROM kg_edges WHERE kind = 'contains' AND src LIKE 'program:%' AND dst LIKE 'year:%'")}
        raw: dict[str, set[int]] = defaultdict(set)
        for src, dst in conn.execute("SELECT src, dst FROM kg_edges WHERE kind = 'contains' AND dst LIKE 'doc:%'"
                                     " AND (src LIKE 'program:%' OR src LIKE 'year:%')"):
            tail = str(dst).split(":")[1]
            if tail.isdigit():
                raw[year_of.get(src, src)].add(int(tail))
        members = [(r[0], r[1]) for r in conn.execute(
            "SELECT src, dst FROM kg_edges WHERE kind = 'contains' AND src LIKE 'group:%' AND dst LIKE 'program:%'")]
    vis = _visible_docs(db, set().union(*raw.values()) if raw else set(), scope)
    out = {k: v & vis for k, v in raw.items() if k.startswith("program:")}
    for g, p in members:
        out.setdefault(g, set()).update(out.get(p, set()))
    _cache[key] = (time.time(), out)
    return out


def programs(db, scope: dict | None = None) -> list[dict]:
    """왼쪽 사업 목록 — 보일 이름은 label_of, 같은 사업으로 묶이는 이름(program_names.group_names)은 한 줄로(문서가 가장 많은
    노드를 열고 수는 합집합), 문서 0건은 뺀다. 수는 program_doc_sets 의 정의."""
    from zzaimy.app.program_names import group_key, group_names

    kg_store.ensure(db)
    sets = program_doc_sets(db, scope)
    with db._conn() as conn:
        progs = [dict(r) for r in conn.execute("SELECT id, type, label FROM kg_nodes WHERE type IN ('program', 'program_group')")]
    rows = [{"id": p["id"], "type": p["type"], "label": label_of(p), "docs": sets.get(p["id"], set())}
            for p in progs if sets.get(p["id"])]
    # 「사업」 낱말은 있지만 고유한 말이 남지 않는 이름(「수익 비수익 사업」 — 절차·일반어뿐)은 사업 하나가 아니라 목록에서 뺀다.
    # 「사업」 낱말이 없는 이름(「…센터」「…체계(앵커)」)은 이 판정의 대상이 아니라 둔다
    from zzaimy.app.program_names import is_program_name
    rows = [r for r in rows if r["type"] != "program" or "사업" not in r["label"] or is_program_name(r["label"])]
    plain = [r for r in rows if r["type"] == "program"]
    # 외부 확인 장부(학교 재정지원사업 현황 포함)가 같은 사업이라 적은 이름은 한 줄로 — 표기·약칭·해마다 바뀐 이름
    from zzaimy.app.program_names import load_ledger
    rep = group_names({r["label"]: len(r["docs"]) for r in plain}, ledger=load_ledger(), known=[r["label"] for r in plain])
    merged: dict[str, dict] = {}
    for r in sorted(plain, key=lambda r: -len(r["docs"])):
        # 사업 이름 꼴이면 group_names 의 대표 이름, 아니면(「…체계(앵커)」처럼 「사업」 낱말이 없는 이름) 같은 열쇠끼리만
        k = ("name:" + rep[r["label"]]) if rep.get(r["label"]) else ("key:" + (group_key(r["label"]) or r["label"]))
        if k in merged:
            merged[k]["docs"] = merged[k]["docs"] | r["docs"]
            merged[k]["ids"].append(r["id"])
        else:
            merged[k] = {**r, "label": rep.get(r["label"]) or r["label"], "ids": [r["id"]]}
    # 장부 항목 하나에 이어지는 이름(정식 이름이 이름 속에 든 것 — 「2차년도(2023) ○○사업」)도 한 줄로. 사업 목록 나무
    # (app.program_tree)와 같은 찾기라 라이브러리·그래프 목록에 같은 사업이 두 줄로 서지 않는다
    from zzaimy.app.program_tree import Ledger
    lg, owner = Ledger(load_ledger()), {}
    for k, r in sorted(merged.items(), key=lambda kv: -len(kv[1]["docs"])):
        e = lg.find(r["label"])
        if e is None:
            continue
        if id(e) in owner:
            top = merged[owner[id(e)]]
            top["docs"] = top["docs"] | r["docs"]
            top["ids"] += r["ids"]
            del merged[k]
        else:
            owner[id(e)] = k
    out = [{"id": r["id"], "type": r["type"], "type_ko": TYPE_KO.get(r["type"], r["type"]), "label": r["label"],
            "n_docs": len(r["docs"]), "ids": r.get("ids", [r["id"]])}
           for r in [x for x in rows if x["type"] == "program_group"] + list(merged.values())]
    return sorted(out, key=lambda p: (p["type"] != "program_group", -p["n_docs"]))


def schema(db) -> dict:
    """온톨로지 구조도 — 노드 종류별 수와 (종류 → 종류, 관계, 기준)별 관계 수. 그래프 재구축 주기보다 짧게 10분 캐시."""
    _fresh(db)
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
    # 문서의 하위 종류(갈래)와 종류별 속성 — 실제 노드에서 센다(속성은 종류마다 앞 300개를 본다)
    with db._conn() as conn:
        sub: dict[str, int] = defaultdict(int)
        for r in conn.execute("SELECT props FROM kg_nodes WHERE type = 'doc'"):
            p = _props(r[0])
            lab = p.get("kind_label") or ""
            sub[_KIND_KO.get(lab, lab) or "갈래 미정"] += 1
        # 기관 업무 문서 — 검토가 「사업 아님」으로 정해 사업·연차에 이어지지 않은 문서(OWL 의 z:InstitutionalDoc, ADR-0056 보강)
        inst = conn.execute("SELECT COUNT(*) FROM kg_nodes n WHERE n.type = 'doc' AND n.props LIKE '%\"status\": \"agent\"%'"
                            " AND NOT EXISTS (SELECT 1 FROM kg_edges e WHERE e.dst = n.id AND e.kind = 'contains')").fetchone()[0]
        if inst:
            sub["기관 업무 문서(사업 아님)"] = int(inst)
        attrs: dict[str, dict[str, int]] = {}
        for t in types:
            seen: dict[str, int] = defaultdict(int)
            rows = conn.execute("SELECT props FROM kg_nodes WHERE type = ? LIMIT 300", (t,)).fetchall()
            for r in rows:
                for k, v in _props(r[0]).items():
                    if v not in (None, "", [], {}):
                        seen[k] += 1
            attrs[t] = {PROP_KO.get(k, k): round(100 * v / max(len(rows), 1)) for k, v in sorted(seen.items(), key=lambda kv: -kv[1])}
    out = {"types": [{"type": t, "type_ko": TYPE_KO.get(t, t), "n": n, "def": TYPE_DEF.get(t, ""), "attrs": attrs.get(t, {})}
                     for t, n in types.items()],
           "rels": rels, "doc_kinds": sorted(({"label": k, "n": v} for k, v in sub.items()), key=lambda x: -x["n"])}
    _cache["schema"] = (time.time(), out)
    return out


def _dev_only(request: Request) -> None:
    """온톨로지(전체 스키마·검사 규칙)는 개발 화면 — 일반 계정에는 탭도 주소도 열지 않는다. 그래프 수는 권한 범위 밖 문서까지 센다."""
    if request.state.role != "dev":
        from fastapi import HTTPException
        raise HTTPException(404, "찾을 수 없습니다.")


@router.get("/graph/explore/schema")
def explore_schema(request: Request):
    _dev_only(request)
    out = dict(schema(request.app.state.db))
    rep = _ontology_dir(request) / "shacl_report.json"
    try:
        out["shacl"] = json.loads(rep.read_text(encoding="utf-8"))          # 표준 검사 결과(scripts/177)
    except (OSError, ValueError):
        out["shacl"] = None
    if out["shacl"]:
        out["shacl"] = dict(out["shacl"], violations=example_labels(request.app.state.db, out["shacl"].get("violations") or [],
                                                                    _scope(request)))
    return JSONResponse(out)


def example_labels(db, violations: list[dict], scope: dict) -> list[dict]:
    """검사 결과의 예시 노드(「doc%3A39223」처럼 주소 꼴로 적힌 id)를 보일 이름으로 — id 는 툴팁용으로만 둔다.
    볼 수 없는 문서의 노드는 이름 대신 「볼 수 없는 문서」."""
    from urllib.parse import unquote

    ids = sorted({unquote(str(x)) for v in violations for x in (v.get("examples") or [])})
    nodes = _load_nodes(db, ids) if ids else {}
    ok = _allowed(db, nodes, scope) if nodes else set()
    out = []
    for v in violations:
        ex = []
        for x in v.get("examples") or []:
            nid = unquote(str(x))
            n = nodes.get(nid)
            label = (label_of(n) if nid in ok else "볼 수 없는 문서") if n else "그래프에 없는 노드"
            ex.append({"id": nid, "label": label})
        out.append(dict(v, examples=ex))
    return out


def _ontology_dir(request: Request):
    """scripts/177 이 쓰는 곳 — 플랫폼 DB 파일 옆 ontology/ (운영 PostgreSQL 이어도 경로 값은 data/platform/platform.db)."""
    from pathlib import Path
    return Path(getattr(request.app.state.db, "path", "data/platform/platform.db")).parent / "ontology"


@router.get("/graph/explore/ontology.ttl")
def ontology_ttl(request: Request):
    """온톨로지 설계도(OWL, Turtle) — 내보낸 파일이 있으면 그것, 없으면 지금 그래프에서 만든다. Protégé·WebVOWL 로 연다."""
    _dev_only(request)
    from fastapi.responses import Response
    f = _ontology_dir(request) / "ontology.ttl"
    if f.is_file():
        body = f.read_text(encoding="utf-8")
    else:
        from zzaimy.graph import ontology_rdf
        body = ontology_rdf.schema_graph(schema(request.app.state.db), _KIND_KO).serialize(format="turtle")
    return Response(body, media_type="text/turtle; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=zzaimy-ontology.ttl"})


@router.get("/graph/explore/shapes.ttl")
def shapes_ttl(request: Request):
    """검사 규칙(SHACL, Turtle)."""
    _dev_only(request)
    from fastapi.responses import Response
    from zzaimy.graph import ontology_rdf
    return Response(ontology_rdf.SHAPES.read_text(encoding="utf-8"), media_type="text/turtle; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=zzaimy-shapes.ttl"})


def overview(db, scope: dict | None = None) -> dict:
    """처음 여는 그림 — 왼쪽 목록과 같은 사업·사업 묶음(programs)을 노드로, 그 사이 관계를 선으로. 노드 크기는 문서 수.
    문서·절·연차·기관 노드는 넣지 않는다 — 사업 100여 개 규모에서도 노드·관계가 수백 안쪽이라 처음 그리기가 가볍다.

    같은 사업으로 묶인 이름(programs 의 ids)은 대표 노드 하나로 모으고, 관계도 대표끼리로 옮긴다(자기 자신으로 가는 선은 뺀다).
    totals 의 문서 수는 사업마다 센 문서(program_doc_sets)의 합집합 — 여러 사업에 걸린 문서도 한 번만 센다."""
    rows = programs(db, scope)
    rep = {i: r["id"] for r in rows for i in r["ids"]}
    sets = program_doc_sets(db, scope)
    ids = sorted(rep)
    edges: dict[tuple, dict] = {}
    with db._conn() as conn:
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for r in conn.execute(f"SELECT src, dst, kind, basis, evidence, weight FROM kg_edges WHERE src IN ({','.join('?' * len(part))})", part):
                r = dict(r)
                a, b = rep.get(r["src"]), rep.get(r["dst"])
                if not a or not b or a == b or (a, b, r["kind"]) in edges:
                    continue
                edges[(a, b, r["kind"])] = {"src": a, "dst": b, "kind": r["kind"], "label": KIND_KO.get(r["kind"], (r["kind"],))[0],
                                            "basis": r["basis"],
                                            "evidence": (_props(r["evidence"])[:2] if isinstance(r["evidence"], str) else [])
                                            if _dev(scope) or _public_evidence(r["kind"], r["src"]) else [],
                                            "weight": r["weight"]}
    kind_of = {r["id"]: r["type"] for r in rows}
    groups_of: dict[str, list[str]] = defaultdict(list)
    for e in edges.values():
        if e["kind"] == "contains" and kind_of[e["src"]] == "program_group" and kind_of[e["dst"]] == "program":
            groups_of[e["dst"]].append(e["src"])
    docs: set[int] = set()
    for r in rows:
        if r["type"] == "program":
            for i in r["ids"]:
                docs |= sets.get(i, set())
    nodes = [{**r, "short": r["label"], "groups": groups_of.get(r["id"], [])} for r in rows]
    return {"nodes": nodes, "edges": list(edges.values()),
            "totals": {"programs": sum(r["type"] == "program" for r in rows),
                       "groups": sum(r["type"] == "program_group" for r in rows), "docs": len(docs)}}


def program_tree(nodes: list[dict]) -> list[dict]:
    """왼쪽 목록의 대·중·소 분류 — 라이브러리 「사업별로 보기」와 같은 나무(app.program_tree). 교육부 재정지원 체계 갈래(중분류)는
    그래프의 사업 묶음 노드와 같은 갈래라 그 노드를 달아 체크로 고를 수 있게 한다."""
    from zzaimy.app import program_tree as pt
    from zzaimy.app.program_names import load_ledger
    tree = pt.build([{**n, "key": n["id"], "n": n["n_docs"]} for n in nodes if n["type"] == "program"], load_ledger())
    groups = [n for n in nodes if n["type"] == "program_group"]
    for t in tree:
        for g in t["groups"]:
            g["node"] = next((n for n in groups if g["label"] and str(n["label"]).startswith(g["label"])), None)
    used = {g["node"]["id"] for t in tree for g in t["groups"] if g.get("node")}
    # 중분류에 달리지 않은 묶음 노드(교육부 체계 밖 묶음 등)도 고를 수 있게 맨 위 「사업 묶음」으로
    rest = [n for n in groups if n["id"] not in used]
    # 묶음의 사업 대부분(8할 이상)이 한 대분류에 있으면 그 대분류 맨 위에 단다(「교육부 체계 밖」 묶음 → 다른 부처·지자체 …)
    l1_of = {it["key"]: t for t in tree for g in t["groups"] for it in g["progs"]}
    for n in list(rest):
        homes = [l1_of[p["id"]]["label"] for p in nodes if p["type"] == "program" and n["id"] in (p.get("groups") or []) and p["id"] in l1_of]
        top = max(set(homes), key=homes.count) if homes else None
        if top and homes.count(top) >= 0.8 * len(homes):
            t = next(t for t in tree if t["label"] == top)
            t["groups"].insert(0, {"label": n["label"], "n": n["n_docs"], "node": n, "progs": []})
            rest.remove(n)
    if rest:
        tree.insert(0, {"label": "사업 묶음", "n": sum(n["n_docs"] for n in rest), "unknown": False,
                        "groups": [{"label": "", "n": 0, "node": None, "progs": rest}]})
    return tree


@router.get("/graph/explore", response_class=HTMLResponse)
def explore_page(request: Request, id: str = "", sel: str = "", q: str = "", tab: str = ""):
    """처음 열면 전체 개요(사업·묶음). sel=노드,노드 는 고른 것만 펼쳐 보기, id= 는 sel 하나와 같다(다른 화면의 「그래프에서 보기」)."""
    st = request.app.state
    ov = overview(st.db, _scope(request))
    picked = [x for x in ([id] if id else []) + [s for s in sel.split(",") if s.strip()] if x]
    return st.templates.TemplateResponse(request, "kg_explore.html", st.page_ctx(request, {
        "q": q, "programs": ov["nodes"], "tree": program_tree(ov["nodes"]), "totals": ov["totals"], "type_ko": TYPE_KO,
        "kind_ko": {k: v[0] for k, v in KIND_KO.items()}, "sel": list(dict.fromkeys(picked)),
        "tab": "onto" if tab == "onto" and request.state.role == "dev" else "kg"}))


@router.get("/graph/explore/overview")
def explore_overview(request: Request):
    return JSONResponse(overview(request.app.state.db, _scope(request)))


@router.get("/graph/explore/neighbors")
def explore_neighbors(request: Request, id: str, more: str = ""):
    offset = {}
    if more:                                    # 「더 보기」 — 그 관계 묶음의 다음 쪽
        key, _, start = more.rpartition("@")
        offset = {key: int(start) if start.isdigit() else 0}
    return JSONResponse(neighbors(request.app.state.db, id, _scope(request), offset=offset))


@router.get("/graph/explore/docs")
def explore_docs(request: Request, id: str, per: int = 6):
    """사업·연차 노드의 문서 — 연차별로 볼 수 있는 문서(최근 것부터 per 건)와 연차별 전체 수. 오른쪽 패널에서 문서로 바로 간다."""
    db = request.app.state.db
    per = max(1, min(int(per), 30))
    with db._conn() as conn:
        if id.startswith("program:"):
            years = [(r[0], r[1]) for r in conn.execute(
                "SELECT n.id, n.label FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst"
                " WHERE e.kind = 'contains' AND e.src = ? AND e.dst LIKE 'year:%'", (id,)).fetchall()]
            srcs = [(id, "연차 미상"), *years]
        elif id.startswith("year:"):
            lab = conn.execute("SELECT label FROM kg_nodes WHERE id = ?", (id,)).fetchone()
            srcs = [(id, lab[0] if lab else id)]
        else:
            return JSONResponse({"years": [], "total": 0})
        rows = []
        for src, ylab in srcs:
            for nid, label, did in conn.execute(
                    "SELECT n.id, n.label, n.doc_id FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst"
                    " WHERE e.kind = 'contains' AND e.src = ? AND e.dst LIKE 'doc:%' AND n.doc_id IS NOT NULL", (src,)).fetchall():
                rows.append((src, ylab, int(did), label))
    ok = _visible_docs(db, {d for _, _, d, _ in rows}, _scope(request))
    out: dict[str, dict] = {}
    for src, ylab, did, label in rows:
        if did not in ok:
            continue
        # 연차 이름은 사업 이름을 앞에 단다(「○○사업 2차년도 (2023)」) — 패널은 그 사업 안이라 연차 부분만
        m = re.search(r"\d+\s*차년도.*$|\(?(?:19|20)\d{2}\)?\s*$", ylab or "")
        g = out.setdefault(src, {"id": src, "label": (m.group(0).strip() if m and src != id else ylab), "n": 0, "docs": []})
        g["n"] += 1
        g["docs"].append({"doc_id": did, "label": label})
    def when(g):                                  # 최근 연도 먼저, 연차 미상(사업에 바로 매단 문서)은 끝
        m = re.findall(r"(?:19|20)\d{2}", g["label"] or "")
        return (g["id"] != id, int(m[-1]) if m else 0)
    years = sorted(out.values(), key=when, reverse=True)
    for g in years:
        g["docs"] = sorted(g["docs"], key=lambda d: -d["doc_id"])[:per]
    return JSONResponse({"years": years, "total": sum(g["n"] for g in years)})


@router.get("/graph/explore/search")
def explore_search(request: Request, q: str = ""):
    return JSONResponse(search(request.app.state.db, q, _scope(request)))
