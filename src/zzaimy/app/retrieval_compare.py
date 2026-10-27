"""검색 방식 비교 — 질문 하나를 사업 문서 검색의 세 방식(풀텍스트 RAG·온톨로지+RAG·온톨로지+RAG에 27B 질문 읽기)으로
나란히 돌려 찾은 문서·걸린 시간·단계를 돌려준다. 관리자 측정 도구(/dev 아래, 미들웨어가 관리자만 들인다).

대화 입력줄의 실험 고르기를 대신한다 — 답변을 만들지 않고 검색만 본다. 시험지 전체 측정은 scripts/188 이 그대로 한다.
방식 전환은 grant_search.graph_mode(운영 기본은 바꾸지 않는다).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Form, HTTPException, Request

router = APIRouter()

MODES = (("fulltext", "풀텍스트 RAG"), ("graph", "온톨로지+RAG"), ("graph_read", "온톨로지+RAG·27B 질문 읽기"))


def _clear_reader_cache() -> None:
    """27B 질문 읽기 캐시를 비운다 — 방식마다 걸린 시간이 실제와 같게(scripts/188 과 같은 처리)."""
    try:
        from zzaimy.graph import question_read
        with question_read._lock:
            question_read._cache.clear()
    except Exception:
        pass


def compare(db, question: str, k: int = 6, depts: list[str] | None = None) -> dict:
    """{question, modes: [{mode, label, sec, hits: [{doc_id, filename, path, relation}], steps, error}]}.
    depts: 사업 문서 부서 범위(None = 전부 — 관리자 측정)."""
    from zzaimy.app import grant_search

    q = (question or "").strip()
    out = []
    for mode, label in MODES:
        _clear_reader_cache()
        t0 = time.time()
        err = ""
        try:
            with grant_search.graph_mode(mode):
                g = grant_search.search(db, q, k=k, user=None, depts=depts)
        except Exception as e:                       # 한 방식의 실패도 결과로 보인다
            g, err = {"hits": [], "steps": []}, f"{type(e).__name__}: {e}"[:200]
        out.append({"mode": mode, "label": label, "sec": round(time.time() - t0, 2), "error": err,
                    "steps": list(g.get("steps") or [])[:8],
                    "hits": [{"doc_id": h.get("doc_id"), "filename": h.get("filename") or "", "path": list(h.get("path") or []),
                              "relation": h.get("relation") or ""} for h in g.get("hits") or []]})
    return {"question": q, "k": k, "modes": out}


@router.post("/dev/quality/retrieval-compare")
def retrieval_compare(request: Request, question: str = Form(""), k: int = Form(6)):
    """질문 하나를 세 검색 방식으로 나란히 — JSON. 품질·성능 화면의 「검색 방식 비교」가 부른다."""
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "관리자 계정만 쓸 수 있습니다")
    if not question.strip():
        raise HTTPException(400, "비교할 질문을 적어 주세요")
    return compare(request.app.state.db, question[:400], k=max(1, min(int(k or 6), 20)))
