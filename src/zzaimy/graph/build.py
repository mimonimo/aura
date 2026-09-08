"""지식 그래프 1단계 — 구조 그래프 (경량 온톨로지 v1, ADR-0009).

DB에 이미 있는 관계를 그래프로 엮는다. LLM·GPU 없이 동작하며,
개체 수준 그래프(2단계)는 LLM 연결 후 이 위에 얹는다.

온톨로지 v1
  노드: criteria(기준 문서) · intake(접수 문서) · project(프로젝트)
  간선: refers(접수→기준 근거) · belongs(문서→프로젝트 소속)
       · uses(프로젝트→기준 적용) · cites(기준→기준 조문 참조)
       · similar(기준↔기준 임베딩 유사)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 임베딩 유사 간선 — 이 값보다 가까운 문서 쌍만 잇는다 (노드당 상위 2개).
# 0.55는 간선이 전체의 95%를 차지해 화면이 어수선했다(실측 52/54) → 강한
# 유사만 남기고, 약한 연결 발견은 검색(RRF) 몫으로 둔다
_SIM_THRESHOLD = 0.75
_SIM_TOP_K = 2
# 조문 참조 스캔 — 너무 짧은 제목은 오탐이 많아 제외
_MIN_TITLE_LEN = 3


def _doc_node(d: dict, kind: str, chunk_counts: dict[int, int]) -> dict:
    label = d["filename"]
    for suffix in (".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".png", ".jpg"):
        if label.lower().endswith(suffix):
            label = label[: -len(suffix)]
            break
    return {
        "id": f"d{d['id']}",
        "doc_id": d["id"],
        "label": label,
        "kind": kind,
        "sector": d.get("sector") or "common",
        "doc_type": d.get("doc_type") or "auto",
        "chunks": chunk_counts.get(d["id"], 0),
    }


def build_graph(db, include_similarity: bool = True) -> dict:
    """DB의 관계를 노드·간선 목록으로 만든다. 반환 형식은 /graph.json 계약."""
    docs = db.list_documents()
    docs = [d for d in docs if d.get("doc_type") != "ocr"]  # OCR 작업물은 제외
    reg_counts = db.regulation_chunk_counts()

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(s: str, t: str, kind: str, w: float = 1.0) -> None:
        if s == t:
            return
        key = (min(s, t), max(s, t), kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"s": s, "t": t, "kind": kind, "w": round(w, 3)})

    criteria = [d for d in docs if d["doc_type"] == "regulation"]
    intake = [d for d in docs if d["doc_type"] != "regulation"]
    doc_ids = {d["id"] for d in docs}

    for d in criteria:
        nodes.append(_doc_node(d, "criteria", reg_counts))
    for d in intake:
        nodes.append(_doc_node(d, "intake", reg_counts))

    # 프로젝트 노드 + 소속·적용 간선
    for p in db.list_all_projects():
        nodes.append({
            "id": f"p{p['id']}",
            "doc_id": None,
            "label": p["name"],
            "kind": "project",
            "sector": p.get("sector") or "common",
            "doc_type": "project",
            "chunks": 0,
        })
        for cid in db.get_project_criteria_ids(p["id"]):
            if cid in doc_ids:
                add_edge(f"p{p['id']}", f"d{cid}", "uses")

    # 접수 문서 → 기준 근거 간선 (지정 + 자동 제안)
    for d in intake:
        if d.get("project_id"):
            add_edge(f"d{d['id']}", f"p{d['project_id']}", "belongs")
        if d.get("related_criteria_id") in doc_ids:
            add_edge(f"d{d['id']}", f"d{d['related_criteria_id']}", "refers")
        raw = d.get("suggested_criteria")
        if raw:
            try:
                for it in json.loads(raw):
                    cid = it.get("id") if isinstance(it, dict) else None
                    if cid in doc_ids:
                        add_edge(f"d{d['id']}", f"d{cid}", "refers", 0.7)
            except (ValueError, AttributeError):
                pass

    _add_citation_edges(db, criteria, add_edge)
    if include_similarity:
        _add_similarity_edges(db, criteria, add_edge)
    _add_entity_layer(db, nodes, doc_ids, add_edge)

    return {"nodes": nodes, "edges": edges}


def _add_entity_layer(db, nodes: list[dict], doc_ids: set[int], add_edge) -> None:
    """개체 계층 (온톨로지 v2) — 두 문서 이상을 잇는 개체만 노드로 올린다.

    한 문서에만 나오는 개체는 그래프에서 연결 가치가 없어 제외한다
    (검색 확장에는 doc_entities 원본을 그대로 쓴다).
    """
    try:
        data = db.graph_entities(min_docs=2)
    except Exception:
        return
    if not data["entities"]:
        return
    kept = set()
    for e in data["entities"]:
        kept.add(e["id"])
        nodes.append({
            "id": f"e{e['id']}",
            "doc_id": None,
            "label": e["name"],
            "kind": "entity",
            "sector": "common",
            "doc_type": e["kind"],   # program | org | year
            "chunks": e["n_docs"],
        })
    for ln in data["links"]:
        if ln["entity_id"] in kept and ln["doc_id"] in doc_ids:
            w = min(1.0, 0.4 + 0.1 * ln["n_mentions"])
            add_edge(f"d{ln['doc_id']}", f"e{ln['entity_id']}", "mentions", w)


def _add_citation_edges(db, criteria: list[dict], add_edge) -> None:
    """기준 문서 조각 본문에 다른 기준의 제목이 등장하면 참조 간선을 잇는다."""
    chunks = db.list_regulation_chunks()
    if not chunks:
        return
    # 문서별 대표 제목: 조각의 reg_title (없으면 건너뜀)
    title_of: dict[int, str] = {}
    for c in chunks:
        title_of.setdefault(c["doc_id"], (c.get("reg_title") or "").strip())
    titles = {
        did: t for did, t in title_of.items() if len(t) >= _MIN_TITLE_LEN
    }
    text_of: dict[int, list[str]] = {}
    for c in chunks:
        text_of.setdefault(c["doc_id"], []).append(c["content"])
    for src_id, parts in text_of.items():
        body = "\n".join(parts)
        for dst_id, title in titles.items():
            if dst_id == src_id:
                continue
            if title in body:
                add_edge(f"d{src_id}", f"d{dst_id}", "cites")


def _add_similarity_edges(db, criteria: list[dict], add_edge) -> None:
    """사전 계산 임베딩(npz)이 있으면 문서 평균 벡터의 코사인으로 유사 간선.

    준비물이 없으면 조용히 건너뛴다 (로컬 개발 환경 등).
    """
    try:
        import numpy as np

        from zzaimy.app.embed_search import INDEX_PATH

        if not Path(INDEX_PATH).exists():
            return
        data = np.load(INDEX_PATH)
        ids, vectors = data["ids"], data["vectors"]
        chunk_doc = {
            c["id"]: c["doc_id"] for c in db.list_regulation_chunks()
        }
        by_doc: dict[int, list] = {}
        for i, cid in enumerate(ids):
            did = chunk_doc.get(int(cid))
            if did is not None:
                by_doc.setdefault(did, []).append(vectors[i])
        if len(by_doc) < 2:
            return
        doc_ids = sorted(by_doc)
        mat = np.stack([np.mean(by_doc[d], axis=0) for d in doc_ids])
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        sim = mat @ mat.T
        for i, src in enumerate(doc_ids):
            order = np.argsort(sim[i])[::-1]
            picked = 0
            for j in order:
                if j == i or picked >= _SIM_TOP_K:
                    continue
                if sim[i][j] < _SIM_THRESHOLD:
                    break
                add_edge(f"d{src}", f"d{doc_ids[j]}", "similar", float(sim[i][j]))
                picked += 1
    except Exception as e:  # 그래프는 부가 기능 — 유사 간선 실패가 전체를 막지 않게
        log.warning("유사 간선 생략 (%s: %s)", type(e).__name__, e)
