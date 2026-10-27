"""반입 결과 보기(/dev/archive/doc/{id}, 관리자) 조립 — 문서 하나가 반입되며 남긴 것을 화면용 dict로 모은다.

개요(접수번호·상태·원본·마스킹 기록), RAG 조각(추출 조각·검색 단위·임베딩 유무), 지식 그래프에서 이어진 문서·개체.
전부 순수 함수다 — DB와 파일 경로를 인자로 받고, 그래프 조회는 호출부가 함수로 넘긴다(테스트에서는 가짜를 넣는다).
옛 데이터 현황 화면(10-27 에 없앰)의 「문서」 탭이 하던 문서 하나 들여다보기를 원본 관리 쪽 한 화면으로 옮겼다.
원본 장부 값은 원본 관리 줄, 기준 문서 목록은 RAG 공간이 맡는다.

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

from zzaimy.app import paths as _paths

META_PATH = _paths.index_meta()

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
NAME_CHARS = 60        # 프로젝트·관련 기준 문서 이름 표시 길이
EXTRACT_PAGE = 100     # 추출 내용은 앞 100건만 펼쳐 두고 나머지는 「더 보기」로


PAGE = "/dev/archive/doc"      # 반입 결과 보기 — 원본 관리의 하위 화면(사이드바에는 없다, app/dev_nav EXTRA)


# --- 공통 ---


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


# 읽기 메모(documents.parse_note)는 반입 때 처리기가 적은 글 그대로 저장돼 있다 — 화면에서는 쉬운 말로만 바꿔 보인다(저장값은 그대로)
_NOTE_WORDS = (
    ("구조 추출 생략 — 이 서버에 GPU 없음", "구조 읽기 생략(GPU 없음)"),
    ("구조 추출 실패", "구조 읽기 실패"),
    ("글자층 직독", "PDF 글자 그대로 읽음"),
    ("AI 비전 판독", "AI 그림 읽기"),
)


def plain_parse_note(note: str) -> str:
    """읽기 메모를 화면용 쉬운 말로 — 「갈라진 낱말 N개 복원」 → 「끊긴 낱말 N개 이음」, 「쪽수 N」 → 「N쪽」."""
    out = note or ""
    for a, b in _NOTE_WORDS:
        out = out.replace(a, b)
    out = re.sub(r"갈라진 낱말 (\d[\d,]*)개 복원", r"끊긴 낱말 \1개 이음", out)
    return re.sub(r"쪽수 (\d[\d,]*)", r"\1쪽", out)


def _project_label(name: str) -> str:
    """보관 묶음 이름의 「(연도 미상)」 꼬리는 화면에서 뺀다 — 연도가 없으면 적지 않는다."""
    return re.sub(r"\s*\(연도 미상\)\s*$", "", name or "")


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
            {"id": project["id"], "name": shorten(_project_label(project.get("name") or ""), NAME_CHARS)}
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
        "parse_note": plain_parse_note(doc.get("parse_note") or ""),
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
        if "n_rows" not in obj and "cells" not in obj:
            return None
        try:
            return f"빈 표({int(obj.get('n_rows') or 0)}×{int(obj.get('n_cols') or 0)})"
        except (TypeError, ValueError):
            return "빈 표"
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


def doc_chunks_view(db, doc_id: int, embedded_ids: frozenset[int] | None = None,
                    doc: dict | None = None) -> dict:
    """문서의 RAG 조각 — 추출 조각(종류별 건수)과 검색 단위(임베딩 건수).

    사업 문서(grant)는 추출 조각의 글을 사업 문서 색인에 그대로 쓰므로 n_grant 로 따로 센다
    (검토 끝난 문서만, 아니면 None).
    """
    raw = db.list_doc_chunks(doc_id)
    extract = chunk_rows(raw)
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
        "n_grant": _grant_count(raw, doc),
    }


def _grant_count(chunks: list[dict], doc: dict | None) -> int | None:
    if not doc or doc.get("doc_type") != "grant" or doc.get("status") != "reviewed":
        return None
    from zzaimy.app.grant_search import TEXT_KINDS
    return sum(1 for c in chunks if (c.get("kind") or "text") in TEXT_KINDS)


NODE_KIND_LABELS = {"criteria": "기준", "intake": "접수", "project": "프로젝트"}
ENTITY_KIND_LABELS = {"program": "사업", "org": "기관", "law": "근거 법령",
                      "year": "연도"}


def node_href(node: dict) -> str:
    nid = str(node.get("id") or "")
    if nid.startswith("d") and node.get("doc_id"):
        return f"/doc/{node['doc_id']}"
    if nid.startswith("p"):
        return f"/project/{nid[1:]}"
    from urllib.parse import quote
    return "/graph/explore?q=" + quote(str(node.get("label") or ""))


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


def related_from_kg(db, doc_id: int, scope: dict) -> dict:
    """문서 노드(doc:N)의 이웃 — 지식 그래프(kg_nodes·kg_edges)에서 바로 읽는다(kg_explore.neighbors, 노드 하나라 1초 안).

    build_graph 로 요청마다 문서 전체 그래프를 새로 만들면 문서가 수만 건일 때 화면이 몇 분씩 멈춘다."""
    from fastapi import HTTPException

    from zzaimy.app import kg_explore

    try:
        d = kg_explore.neighbors(db, f"doc:{doc_id}", scope, per_group=20)
    except HTTPException:
        return {"available": True, "in_graph": False, "groups": [], "n": 0}
    nodes = {n["id"]: n for n in d["nodes"]}
    out = []
    for g in d["groups"]:
        items = []
        for e in d["edges"]:
            other = e["dst"] if e["src"] == f"doc:{doc_id}" else e["src"]
            n = nodes.get(other)
            if e["kind"] != g["kind"] or n is None or n["type"] != g["type"] \
                    or (g["dir"] == "out") != (e["src"] == f"doc:{doc_id}"):
                continue
            href = f"/doc/{n['doc_id']}" if n["type"] == "doc" and n.get("doc_id") is not None \
                else "/graph/explore?id=" + n["id"]
            items.append({"id": n["id"], "label": n["label"], "node_kind": n["type"], "kind_label": n["type_ko"],
                          "doc_type": "", "w": float(e.get("weight") or 1), "note": e.get("basis") or "",
                          "why": "", "evidence": e.get("evidence") or [], "href": href})
        if items:
            out.append({"kind": g["key"], "label": g["label"] + (f" (전체 {g['total']:,})" if g["total"] > len(items) else ""),
                        "nodes": items})
    return {"available": True, "in_graph": True, "groups": out, "n": sum(len(g["nodes"]) for g in out)}


def doc_result(
    db, doc: dict,
    graph_fn: Callable[[], dict | None] | None = None,
    related_fn: Callable[[int], dict] | None = None,
    index: dict | None = None,
    file_exists: Callable[[str], bool] | None = None,
) -> dict:
    """문서 하나의 반입 결과 — 개요·추출·검색 자료·그래프 연관."""
    ids = (index or {}).get("ids")
    did = int(doc["id"])
    return {
        "overview": doc_overview(db, doc, file_exists),
        "chunks": doc_chunks_view(db, did, ids, doc),
        "related": related_fn(did) if related_fn else related_for_doc(graph_fn() if graph_fn else None, did),
    }
