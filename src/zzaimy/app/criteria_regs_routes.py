"""기준 문서 관리(/criteria/regs*) — 라이브러리 「기준」 칩에서 관리자가 규정의 학생 공개·검색에서 빼기·새 판 등록을 한다.

교직원은 「기준」 칩에서 기준 문서를 보기만 하고(지금처럼), 관리자(dev)만 이 경로를 쓴다. /dev 경로가 아니라 로그인 검사(main.check_auth)가
역할을 막지 않으므로 처리마다 역할을 서버에서 다시 본다(_need_admin). 등록·해제·학생 공개는 regulation_register 그대로(ADR-0064).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _need_admin(request: Request) -> None:
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "관리자만 기준 문서를 관리합니다")


@router.get("/criteria/regs")
def criteria_regs(request: Request, q: str = ""):
    """검색에 쓰는 규정(registered)과 새 판·미등록 후보(items·total), 다시 색인이 필요한지."""
    _need_admin(request)
    from zzaimy.app import regulation_register, reindex_state
    from zzaimy.app.doc_title import display_title     # 화면에는 확장자 뗀 제목, 파일 이름은 filename 에 그대로
    st = request.app.state
    got = regulation_register.candidates(st.db, q)
    for d in got["registered"]:
        d["title"] = display_title({"filename": d.get("filename") or ""}) or d.get("filename") or ""
    for d in got["items"]:
        d["filename"] = d.get("title") or ""
        d["title"] = display_title({"filename": d["filename"]}) or d["filename"]
    return {**got, "reindex_needed": reindex_state.needed(st.db), "reindex_running": st.reindex_running()}


@router.get("/criteria/regs/{did}")
def criteria_reg_detail(request: Request, did: int):
    """규정 한 건의 상세 — 조각 수·조 머리 앞부분·판(제목·본문의 날짜·학년도·번호)·같은 규정의 다른 판. 누를 때 센다."""
    _need_admin(request)
    from zzaimy.app import regulation_register as rr
    from zzaimy.app.doc_title import display_title
    db = request.app.state.db
    d = db.get_document(did)
    if not d or d.get("doc_type") not in ("regulation", "grant"):
        raise HTTPException(404, "규정을 찾지 못했습니다")
    with db._conn() as conn:
        heads = [str(r[0] or "") for r in conn.execute(
            "SELECT heading FROM regulation_chunks WHERE doc_id = ? ORDER BY id", (did,))]
        key = rr.rule_key(d.get("filename") or "")
        mates = [dict(r) for r in conn.execute(
            "SELECT id, filename, family, doc_type, created_at FROM documents WHERE doc_type IN ('regulation', 'grant')"
            " AND status = 'reviewed' AND id <> ? AND (" + " OR ".join("filename LIKE ?" for _ in rr.GENRE_WORDS) + ")",
            [did, *[f"%{w}%" for w in rr.GENRE_WORDS]])] if key else []
    texts = rr._texts(db, [did])

    def label(rev: tuple) -> str:
        acad, no, (y, m, dd) = rev
        parts = ([f"{y}.{m}.{dd}" if m and dd else f"{y}년"] if y else []) + ([f"{acad}학년도"] if acad else []) + ([f"제{no}호"] if no else [])
        return " · ".join(parts)
    rev = label(rr.revision(d.get("filename") or "", d.get("family") or "", texts.get(did, "")))
    versions = [{"id": m["id"], "title": display_title({"filename": m["filename"] or ""}) or m["filename"] or "",
                 "registered": m["doc_type"] == "regulation", "at": str(m.get("created_at") or "")[:10]}
                for m in mates if rr.rule_key(m["filename"] or "") == key]
    return {"id": did, "title": display_title({"filename": d.get("filename") or ""}) or d.get("filename") or "",
            "filename": d.get("filename") or "", "registered": d.get("doc_type") == "regulation",
            "kind": d.get("kind") or "", "audience": d.get("audience") or "", "dept": d.get("dept") or "공통",
            "at": str(d.get("created_at") or "")[:10], "chunks": len(heads),
            "headings": [h for h in dict.fromkeys(heads) if h][:8], "revision": rev, "versions": versions[:20]}


async def _ids_from(request: Request) -> list[int]:
    body = await request.json()
    return [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:500]


@router.post("/criteria/regs/register")
async def criteria_regs_register(request: Request):
    _need_admin(request)
    from zzaimy.app import regulation_register
    st = request.app.state
    return regulation_register.register(st.db, await _ids_from(request), st.data_dir, by=request.state.user or "")


@router.post("/criteria/regs/unregister")
async def criteria_regs_unregister(request: Request):
    _need_admin(request)
    from zzaimy.app import regulation_register
    st = request.app.state
    return regulation_register.unregister(st.db, await _ids_from(request), st.data_dir, by=request.state.user or "")


@router.post("/criteria/regs/audience")
async def criteria_regs_audience(request: Request):
    """학생 공개(학생 RAG 에 넣기)·교직원만 — 기준 문서(regulation)만 바뀐다."""
    _need_admin(request)
    db = request.app.state.db
    body = await request.json()
    aud = "student" if body.get("audience") == "student" else "staff"
    n = 0
    for did in [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:500]:
        d = db.get_document(did)
        if d and d.get("doc_type") == "regulation":
            db.set_document_audience(did, aud)
            n += 1
    return {"changed": n, "audience": aud}
