"""데이터 열람(/dev/db) 조립 — 문서·규정·국고 코퍼스·채팅 기록을 화면용 dict로 모은다.

행·열 덤프 대신 문서 하나를 축으로 본다: 개요(접수번호·상태·원본·마스킹 기록),
RAG 조각(추출 조각·검색 단위·임베딩 유무), 지식 그래프에서 이어진 문서·개체.
전부 순수 함수다 — DB와 파일 경로를 인자로 받고, 모델이 필요한 검색·그래프 생성은
호출부가 함수로 넘긴다(테스트에서는 가짜를 넣는다).

원문 개인정보 값은 어디에도 내지 않는다: 본문은 저장된 마스킹본(doc_chunks·
regulation_chunks)만, 마스킹 기록은 유형·건수만 낸다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from zzaimy.app.embed_search import INDEX_PATH
from zzaimy.app.pii_audit import ENTITY_LABELS, is_masking_subject

META_PATH = Path("data/platform/chunk_embeddings.meta.json")

TABS: list[tuple[str, str]] = [
    ("docs", "문서"),
    ("regulation", "규정"),
    ("corpus", "국고 코퍼스"),
    ("chat", "채팅 기록"),
]
_TAB_KEYS = {k for k, _ in TABS}

# doc_chunks.kind — 파서 산출 조각의 종류
KIND_LABELS = {
    "text": "본문",
    "heading": "제목",
    "table": "표",
    "image": "그림",
    "image_text": "그림 속 글자",
}

# 그래프 간선 종류(zzaimy.graph.build) — 화면 표시 순서와 이름
EDGE_LABELS: dict[str, str] = {
    "refers": "근거 (접수→기준)",
    "belongs": "소속 프로젝트",
    "uses": "적용 프로젝트",
    "cites": "조문 참조",
    "similar": "유사 (임베딩)",
    "of_program": "이 문서의 사업",
    "same_program": "같은 사업 문서",
    "same_law": "같은 근거 법령",
    "relates": "추정 연관 (프로젝트)",
    "mentions": "언급 개체",
}

# 가중치가 뜻을 갖는 간선 — 코사인·TF-IDF 값. 구조 간선(근거·소속·적용·조문 참조)은 표시하지 않는다
WEIGHTED_EDGES = {"similar", "relates", "mentions"}

PREVIEW_CHARS = 90     # 목록 미리보기 길이 — 펼치면 전문
HIT_PREVIEW_CHARS = 200
NAME_CHARS = 60        # 목록의 파일명 표시 길이
LIST_LIMIT = 500


# --- 공통 ---


def normalize_tab(tab: str, table: str = "") -> str:
    """탭 이름 정규화. 예전 표 브라우저 주소(?table=…)는 가장 가까운 탭으로 보낸다."""
    if tab in _TAB_KEYS:
        return tab
    if table.startswith("regulation"):
        return "regulation"
    if table.startswith("chat"):
        return "chat"
    return "docs"


def shorten(text: str | None, n: int = PREVIEW_CHARS) -> str:
    """공백을 한 칸으로 접고 n자에서 자른다(말줄임표 포함)."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) <= n:
        return t
    return t[: max(n - 1, 1)].rstrip() + "…"


def _stem(filename: str) -> str:
    return Path(filename or "").stem or (filename or "")


_index_cache: dict = {}


def embedding_index(meta_path: Path = META_PATH, npz_path: Path = INDEX_PATH) -> dict:
    """임베딩 색인 요약 — meta.json(모델·조각 수·차원)과 npz의 조각 id 집합.

    meta가 없으면 색인 없음. npz를 읽지 못하면 ids=None — 조각별 판정은 미확인으로
    두고 색인 요약만 보인다. 파일 수정 시각으로 캐시한다.
    """
    meta_path, npz_path = Path(meta_path), Path(npz_path)
    try:
        key = (
            str(meta_path), meta_path.stat().st_mtime if meta_path.exists() else None,
            str(npz_path), npz_path.stat().st_mtime if npz_path.exists() else None,
        )
    except OSError:
        key = (str(meta_path), None, str(npz_path), None)
    cached = _index_cache.get("value")
    if cached is not None and _index_cache.get("key") == key:
        return cached

    out: dict = {"exists": False, "model": "", "n_chunks": 0, "dim": None,
                 "ids": None, "updated_at": None}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = None
        if isinstance(meta, dict):
            out.update({
                "exists": True,
                "model": str(meta.get("model") or ""),
                "n_chunks": int(meta.get("n_chunks") or 0),
                "dim": meta.get("dim"),
                "updated_at": datetime.fromtimestamp(
                    meta_path.stat().st_mtime).isoformat(timespec="minutes"),
            })
    if out["exists"] and npz_path.exists():
        try:
            import numpy as np

            out["ids"] = frozenset(int(i) for i in np.load(npz_path)["ids"])
        except Exception:
            out["ids"] = None
    _index_cache["key"] = key
    _index_cache["value"] = out
    return out


# --- 문서 ---


def type_counts(db) -> list[dict]:
    """문서 유형별 건수 — 필터 선택지."""
    with db._conn() as conn:  # noqa: SLF001 — 읽기 전용 집계
        rows = conn.execute(
            "SELECT doc_type, COUNT(*) FROM documents GROUP BY doc_type ORDER BY 2 DESC"
        ).fetchall()
    return [{"doc_type": r[0] or "", "n": int(r[1])} for r in rows]


def list_docs(db, q: str = "", doc_type: str = "", limit: int = LIST_LIMIT) -> list[dict]:
    """문서 목록 — 접수번호·파일명·유형·상태·조각 수(추출 조각·검색 단위)."""
    sql = (
        "SELECT d.id, d.receipt_no, d.filename, d.doc_type, d.status, d.decision,"
        " d.sector, d.dept, d.owner, d.created_at, d.project_id,"
        " (SELECT COUNT(*) FROM doc_chunks c WHERE c.doc_id = d.id) AS n_chunks,"
        " (SELECT COUNT(*) FROM regulation_chunks r WHERE r.doc_id = d.id) AS n_units"
        " FROM documents d"
    )
    cond: list[str] = []
    params: list = []
    if doc_type:
        cond.append("d.doc_type = ?")
        params.append(doc_type)
    if q.strip():
        cond.append("(d.filename LIKE ? OR COALESCE(d.receipt_no, '') LIKE ?)")
        params += [f"%{q.strip()}%"] * 2
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY d.id DESC LIMIT ?"
    params.append(int(limit))
    with db._conn() as conn:  # noqa: SLF001 — 본문 컬럼 없이 가볍게 읽는다
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["name"] = shorten(r["filename"], NAME_CHARS)
    return rows


def mask_summary(db, doc: dict) -> dict:
    """문서의 마스킹 기록 — 유형별 건수만(문맥·원문 값은 내지 않는다)."""
    events = db.list_mask_events(int(doc["id"]))
    rows = [
        {"entity_type": e["entity_type"],
         "label": ENTITY_LABELS.get(e["entity_type"], e["entity_type"]),
         "n": int(e["n"])}
        for e in events if int(e["n"]) > 0
    ]
    return {
        "subject": is_masking_subject(doc.get("doc_type"), doc.get("owner")),
        "recorded": bool(events),
        "rows": rows,
        "total": sum(r["n"] for r in rows),
        "recorded_at": events[0]["created_at"] if events else None,
    }


def doc_overview(db, doc: dict, file_exists: Callable[[str], bool] | None = None) -> dict:
    """개요 — 접수번호·유형·상태·시각·원본 파일 유무·본문 크기·마스킹 기록."""
    path = doc.get("stored_path") or ""
    exists = (file_exists or (lambda p: bool(p) and Path(p).exists()))(path)
    masked = doc.get("masked_text") or ""
    project = db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
    crit = (
        db.get_document(int(doc["related_criteria_id"]))
        if doc.get("related_criteria_id") else None
    )
    return {
        "id": doc["id"],
        "receipt_no": doc.get("receipt_no") or "",
        "filename": doc.get("filename") or "",
        "doc_type": doc.get("doc_type") or "auto",
        "status": doc.get("status") or "",
        "decision": doc.get("decision") or "",
        "sector": doc.get("sector") or "common",
        "dept": doc.get("dept") or "공통",
        "owner": doc.get("owner") or "",
        "created_at": doc.get("created_at") or "",
        "project_id": doc.get("project_id"),
        "project": (
            {"id": project["id"], "name": shorten(project.get("name") or "", NAME_CHARS)}
            if project else None
        ),
        "related_criteria_id": doc.get("related_criteria_id"),
        "related_criteria": (
            {"id": crit["id"], "filename": crit.get("filename") or "",
             "name": shorten(_stem(crit.get("filename") or ""), NAME_CHARS)}
            if crit else None
        ),
        "original": {"exists": bool(exists),
                     "suffix": Path(path).suffix.lower().lstrip("."),
                     "name": Path(path).name},
        "text_chars": len(masked),
        "has_review": bool(doc.get("ai_review")),
        "has_draft": bool(doc.get("draft")),
        "parse_note": doc.get("parse_note") or "",
        "error": shorten(doc.get("error") or "", 160),
        "mask": mask_summary(db, doc),
    }


def table_text(content: str) -> str | None:
    """표 조각(JSON: n_rows·n_cols·cells[..., 글자])을 사람이 읽는 줄로. 표가 아니면 None."""
    if not content.lstrip().startswith("{"):
        return None
    try:
        obj = json.loads(content)
        cells = obj.get("cells") or []
    except (ValueError, AttributeError):
        return None
    rows: dict[int, list[str]] = {}
    for cell in cells:
        if not isinstance(cell, (list, tuple)) or len(cell) < 2:
            continue
        text = str(cell[-1]).strip()
        if text:
            rows.setdefault(int(cell[0]) if isinstance(cell[0], int) else 0, []).append(text)
    if not rows:
        return None
    return "\n".join(" | ".join(rows[r]) for r in sorted(rows))


def chunk_rows(chunks: Iterable[dict]) -> list[dict]:
    """doc_chunks(추출 조각) → 순서·종류·쪽·길이·미리보기·전문(마스킹본). 표는 칸 글자를 이어 보인다."""
    out = []
    for c in chunks:
        content = c.get("content") or ""
        kind = c.get("kind") or "text"
        if kind == "table":
            content = table_text(content) or content
        out.append({
            "id": c.get("id"),
            "seq": c.get("seq"),
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "page_no": c.get("page_no"),
            "length": len(content),
            "preview": shorten(content),
            "content": content,
            "long": len(content) > PREVIEW_CHARS,
        })
    return out


def unit_rows(units: Iterable[dict], embedded_ids: frozenset[int] | None) -> list[dict]:
    """regulation_chunks(검색 단위) → 표제·길이·품질·임베딩 유무·미리보기·전문.

    embedded_ids가 None이면 조각별 판정은 미확인(embedded=None).
    품질은 chunk_quality 판정을 그대로 쓴다 — 검색에서 근거로 쓰일지(usable)와
    그렇지 않다면 왜인지(quality_why)를 같은 기준으로 보여 준다.
    """
    from zzaimy.app import chunk_quality as cq

    units = list(units)
    verdicts = cq.assess_all(units)
    out = []
    for u, v in zip(units, verdicts):
        content = u.get("content") or ""
        uid = u.get("id")
        out.append({
            "id": uid,
            "doc_id": u.get("doc_id"),
            "heading": u.get("heading") or "",
            "reg_title": u.get("reg_title") or "",
            "length": len(content),
            "substantive": cq.substantive_len(content),
            "preview": shorten(content),
            "content": content,
            "long": len(content) > PREVIEW_CHARS,
            "embedded": None if embedded_ids is None else (uid in embedded_ids),
            "quality": v.score,
            "quality_why": v.why(),
            "usable": v.keep(cq.Strictness.SEARCH),
        })
    return out


def doc_chunks_view(db, doc_id: int, embedded_ids: frozenset[int] | None = None) -> dict:
    """문서의 RAG 조각 — 추출 조각(종류별 건수)과 검색 단위(임베딩 건수)."""
    extract = chunk_rows(db.list_doc_chunks(doc_id))
    units = unit_rows(db.chunks_for_docs([doc_id]), embedded_ids)
    counts = Counter(r["kind"] for r in extract)
    kinds = [
        {"kind": k, "label": KIND_LABELS.get(k, k), "n": n}
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    n_embedded = (
        None if embedded_ids is None else sum(1 for u in units if u["embedded"])
    )
    return {
        "extract": extract, "units": units, "kinds": kinds,
        "n_extract": len(extract), "n_units": len(units), "n_embedded": n_embedded,
        "n_usable": sum(1 for u in units if u["usable"]),
    }


NODE_KIND_LABELS = {"criteria": "기준", "intake": "접수", "project": "프로젝트"}
ENTITY_KIND_LABELS = {"program": "사업", "org": "기관", "law": "근거 법령",
                      "year": "연도"}


def node_href(node: dict) -> str:
    nid = str(node.get("id") or "")
    if nid.startswith("d") and node.get("doc_id"):
        return f"/doc/{node['doc_id']}"
    if nid.startswith("p"):
        return f"/project/{nid[1:]}"
    return f"/graph?focus={nid}"


def node_kind_label(node: dict) -> str:
    kind = node.get("kind") or ""
    if kind == "entity":
        return ENTITY_KIND_LABELS.get(node.get("doc_type") or "", "개체")
    return NODE_KIND_LABELS.get(kind, kind)


def related_for_doc(graph: dict | None, doc_id: int) -> dict:
    """그래프(/graph.json 계약)에서 d{doc_id}와 이어진 노드를 간선 종류별로 묶는다.

    graph가 None이면 미생성. 문서가 그래프에 없으면(OCR 작업물 등) in_graph=False.
    """
    if graph is None:
        return {"available": False, "in_graph": False, "groups": [], "n": 0}
    me = f"d{doc_id}"
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    groups: dict[str, list[dict]] = {}
    for e in graph.get("edges", []):
        s, t = e.get("s"), e.get("t")
        if me not in (s, t):
            continue
        other = nodes.get(t if s == me else s)
        if other is None:
            continue
        kind = e.get("kind", "")
        w = float(e.get("w", 1.0))
        if kind in WEIGHTED_EDGES:
            note = f"{w:.2f}"
        elif kind == "refers" and w < 1:
            note = "자동 제안"          # build_graph: suggested_criteria 간선은 0.7
        else:
            note = ""
        groups.setdefault(kind, []).append({
            "id": other["id"],
            "label": other.get("label") or other["id"],
            "node_kind": other.get("kind") or "",
            "kind_label": node_kind_label(other),
            "doc_type": other.get("doc_type") or "",
            "w": w,
            "note": note,
            # 왜 이어졌는지 — 간선마다 근거가 있어야 한다(graph.build.add_edge)
            "why": e.get("why") or "",
            "evidence": e.get("evidence") or [],
            "href": node_href(other),
        })
    order = list(EDGE_LABELS) + sorted(k for k in groups if k not in EDGE_LABELS)
    out = []
    for k in order:
        items = groups.get(k)
        if not items:
            continue
        out.append({
            "kind": k, "label": EDGE_LABELS.get(k, k),
            "nodes": sorted(items, key=lambda i: (-i["w"], i["label"])),
        })
    return {"available": True, "in_graph": me in nodes, "groups": out,
            "n": sum(len(g["nodes"]) for g in out)}


def docs_tab(
    db, q: str = "", doc_type: str = "", doc_id: int | None = None,
    graph_fn: Callable[[], dict | None] | None = None,
    index: dict | None = None,
    file_exists: Callable[[str], bool] | None = None,
) -> dict:
    """문서 탭 — 목록(검색·유형 필터)과 선택 문서의 개요·조각·연관."""
    view: dict = {
        "docs": list_docs(db, q=q, doc_type=doc_type),
        "types": type_counts(db),
        "selected": None,
    }
    doc = db.get_document(doc_id) if doc_id else None
    if doc is None and doc_id is None and view["docs"]:
        doc = db.get_document(int(view["docs"][0]["id"]))     # 빈 상세 패널 대신 첫 문서를 바로 연다
    view["selected_id"] = int(doc["id"]) if doc else None
    if doc:
        ids = (index or {}).get("ids")
        view["selected"] = {
            "overview": doc_overview(db, doc, file_exists),
            "chunks": doc_chunks_view(db, int(doc["id"]), ids),
            "related": related_for_doc(graph_fn() if graph_fn else None, int(doc["id"])),
        }
    return view


# --- 규정 ---


def regulation_docs(db, embedded_ids: frozenset[int] | None = None) -> list[dict]:
    """규정 문서 목록 — 제목·조각 수·임베딩된 조각 수·계열·부서."""
    with db._conn() as conn:  # noqa: SLF001
        docs = [dict(r) for r in conn.execute(
            "SELECT d.id, d.receipt_no, d.filename, d.sector, d.dept, d.status,"
            " d.owner, d.created_at, COUNT(r.id) AS n_units,"
            " MIN(r.reg_title) AS reg_title"
            " FROM documents d LEFT JOIN regulation_chunks r ON r.doc_id = d.id"
            " WHERE d.doc_type = 'regulation' GROUP BY d.id ORDER BY d.id DESC"
        ).fetchall()]
        emb: dict[int, int] = {}
        if embedded_ids is not None:
            for doc_id, cid in conn.execute("SELECT doc_id, id FROM regulation_chunks"):
                if int(cid) in embedded_ids:
                    emb[int(doc_id)] = emb.get(int(doc_id), 0) + 1
    for d in docs:
        d["title"] = d.get("reg_title") or _stem(d["filename"])
        d["name"] = shorten(d["title"], NAME_CHARS)
        d["n_embedded"] = None if embedded_ids is None else emb.get(int(d["id"]), 0)
    return docs


def search_hits(hits: Iterable[dict], preview_chars: int = HIT_PREVIEW_CHARS) -> list[dict]:
    """검색 결과 조각 → 순위·문서·표제·점수(있으면)·미리보기."""
    out = []
    for rank, h in enumerate(hits, start=1):
        out.append({
            "rank": rank,
            "id": h.get("id"),
            "doc_id": h.get("doc_id"),
            "reg_title": h.get("reg_title") or "",
            "heading": h.get("heading") or "",
            "score": h.get("score"),
            "preview": shorten(h.get("content") or "", preview_chars),
        })
    return out


def regulation_tab(
    db, q: str = "", doc_id: int | None = None,
    search_fn: Callable[[str], list[dict]] | None = None,
    index: dict | None = None,
) -> dict:
    """규정 탭 — 규정 문서 목록, 검색 결과, 선택 규정의 조각(조문 단위)."""
    ids = (index or {}).get("ids")
    hits = search_hits(search_fn(q)) if (q.strip() and search_fn) else []
    view: dict = {"docs": regulation_docs(db, ids), "hits": hits, "selected": None}
    doc = db.get_document(doc_id) if doc_id else None
    if doc and doc.get("doc_type") == "regulation":
        hit_ids = {h["id"] for h in hits}
        units = unit_rows(db.chunks_for_docs([int(doc["id"])]), ids)
        for u in units:
            u["hit"] = u["id"] in hit_ids
        view["selected"] = {
            "id": doc["id"],
            "filename": doc.get("filename") or "",
            "title": (units[0]["reg_title"] if units else "") or _stem(doc.get("filename") or ""),
            "receipt_no": doc.get("receipt_no") or "",
            "sector": doc.get("sector") or "common",
            "dept": doc.get("dept") or "공통",
            "created_at": doc.get("created_at") or "",
            "units": units,
            "n_units": len(units),
            "n_embedded": None if ids is None else sum(1 for u in units if u["embedded"]),
        }
    return view


# --- 국고 코퍼스 ---


def corpus_summary(cdb, top: int = 15) -> dict:
    """코퍼스 파일럿 DB 요약 — 문서·조각 수, 조각 많은 문서 상위."""
    if cdb is None:
        return {"exists": False, "docs": 0, "chunks": 0, "by_source": []}
    with cdb._conn() as conn:  # noqa: SLF001
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM regulation_chunks").fetchone()[0]
        by_source = [
            {"name": r[0], "n": int(r[1])} for r in conn.execute(
                "SELECT d.filename, COUNT(*) FROM regulation_chunks r"
                " JOIN documents d ON d.id = r.doc_id"
                " GROUP BY r.doc_id ORDER BY 2 DESC LIMIT ?", (int(top),))
        ]
    return {"exists": True, "docs": int(docs), "chunks": int(chunks), "by_source": by_source}


def corpus_tab(
    cdb, q: str = "", search_fn: Callable[[str], list[dict]] | None = None,
    dense_ready: bool = False,
) -> dict:
    """국고 코퍼스 탭 — 요약·검색 결과·수집 문서."""
    summary = corpus_summary(cdb)
    results = (
        search_hits(search_fn(q)) if (summary["exists"] and q.strip() and search_fn) else []
    )
    return {"summary": summary, "results": results, "dense_ready": bool(dense_ready)}


# --- 채팅 기록 ---


def chat_sessions_view(
    db, sources_by_session: dict[int, list] | None = None, limit: int = 200,
) -> list[dict]:
    """채팅 세션 — 질문(제목)·시각·메시지 수·근거 수(이번 프로세스가 기억하는 세션만)."""
    with db._conn() as conn:  # noqa: SLF001
        rows = [dict(r) for r in conn.execute(
            "SELECT s.id, s.title, s.created_at, s.owner, s.project_id,"
            " p.name AS project_name,"
            " (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS n_messages,"
            " (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id"
            "   AND m.role = 'assistant') AS n_answers"
            " FROM chat_sessions s LEFT JOIN projects p ON p.id = s.project_id"
            " ORDER BY s.id DESC LIMIT ?", (int(limit),)
        ).fetchall()]
    for r in rows:
        src = (sources_by_session or {}).get(int(r["id"]))
        r["n_sources"] = None if src is None else len(src)
    return rows


def chat_tab(db, sources_by_session: dict[int, list] | None = None) -> dict:
    return {"sessions": chat_sessions_view(db, sources_by_session)}
