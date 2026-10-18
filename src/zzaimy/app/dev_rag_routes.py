"""RAG 공간 관리(/dev/rag*, ADR-0053) — 공간별 자료 수·쓰는 계정, 계정별 추가 권한, 부서 RAG, 규정·기준 문서, 검색 범위 시험.

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·데이터 폴더(data_dir)·계정(accounts)·화면 틀·화면 공통 맥락(page_ctx)과 다른 화면과 함께 쓰는 도우미(부서 목록 all_depts·
  재색인 진행 여부 reindex_running)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


# RAG 공간 관리(ADR-0053) — 공간별 자료 수·쓰는 계정, 계정별 추가 권한, 폴더→부서 짝, 학생 공개 규정. 개발자만(/dev 미들웨어)

@router.get("/dev/rag", response_class=HTMLResponse)
def dev_rag(request: Request):
    """RAG 공간 — 검색 공간·규정·기준 문서·계정별 추가 권한·검색 범위 시험(목록은 /dev/rag/regs 로 따로 불러온다).
    원본 폴더별 부서는 라이브러리 폴더의 「공개 범위 바꾸기」 에서 바꾸고 여기 「RAG 목록」 의 반입 폴더 칸에서 본다."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    db, accounts = st.db, st.accounts
    data_dir = st.data_dir
    cfg = rag_spaces.config(data_dir)
    grants = cfg.get("grants", {})
    accts = [{"id": u, "role": a.get("role", "staff"), "dept": a.get("dept", ""),
              "space": rag_spaces.resolve(a.get("role", "staff"), a.get("dept"), data_dir)["id"],
              "all_grant": rag_spaces.resolve(a.get("role", "staff"), a.get("dept"), data_dir).get("grant_depts") is None,
              "grants": grants.get(u, [])} for u, a in sorted(accounts.items())]
    spaces = rag_spaces.stats(db, data_dir, accounts)
    return st.templates.TemplateResponse(request, "dev_rag.html", st.page_ctx(request, {
        "spaces": spaces, "cfg": cfg, "accts": accts, "dept_spaces": rag_spaces.dept_spaces(cfg, accounts),
        "dict_grants": {a["id"]: {"space": a["space"], "grants": a["grants"]} for a in accts},
        "dept_rags": rag_spaces.dept_rag_rows(db, cfg, accounts, st.all_depts())}))

@router.post("/dev/rag/grant")
async def dev_rag_grant(request: Request):
    """계정 × 부서 공간 표 저장 — 화면에서 바뀐 칸만 지금 설정에 반영한다(다른 사람이 그사이 바꾼 칸은 그대로). 학생·관리자 계정은 대상이 아니다."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    accounts = st.accounts
    data_dir = st.data_dir
    form = await request.form()
    cfg = dict(rag_spaces.config(data_dir))
    grants, done, bad = rag_spaces.grant_changes(cfg, accounts, [str(x) for x in form.getlist("cell")],
                                                 [str(x) for x in form.getlist("was")], [str(x) for x in form.getlist("on")],
                                                 data_dir)
    if bad:
        return RedirectResponse("/dev/rag?" + urlencode({"err": "저장하지 않았습니다 — " + ", ".join(
            f"{k.replace(chr(9), ' · ')}({why})" for k, why in bad[:5])}) + "#grants", status_code=303)
    if not done:
        return RedirectResponse("/dev/rag?" + urlencode({"ok": "바뀐 칸이 없습니다"}) + "#grants", status_code=303)
    cfg["grants"] = grants
    rag_spaces.save_config(cfg, data_dir)
    on = [f"{u} → {sid[5:]}" for u, sid, w in done if w]
    off = [f"{u} → {sid[5:]}" for u, sid, w in done if not w]
    msg = f"추가 권한 {len(done)}건 저장" + (f" · 켬: {', '.join(on)}" if on else "") + (f" · 끔: {', '.join(off)}" if off else "")
    return RedirectResponse("/dev/rag?" + urlencode({"ok": msg}) + "#grants", status_code=303)

@router.post("/dev/rag/dept")
async def dev_rag_dept(request: Request):
    """부서 RAG 만들기·지우기 — action=create(name) | delete(name, 든 문서는 공통 RAG 로)."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    db, accounts = st.db, st.accounts
    data_dir = st.data_dir
    form = await request.form()
    action, name = str(form.get("action") or ""), " ".join(str(form.get("name") or "").split())
    cfg = dict(rag_spaces.config(data_dir))
    if action == "create":
        new, err = rag_spaces.create_dept_rag(cfg, name, by=request.state.user or "")
        if err:
            return RedirectResponse("/dev/rag?" + urlencode({"err": err}) + "#dept-rags", status_code=303)
        rag_spaces.save_config(new, data_dir)
        return RedirectResponse("/dev/rag?" + urlencode({"ok": f"「{name}」 부서 RAG 를 만들었습니다 — 문서 화면·문서 올리기에서 공개 범위를 「부서만」으로 고르면 들어갑니다"})
                                + "#dept-rags", status_code=303)
    if action == "delete" and name in st.all_depts():
        if any(str(a.get("dept") or "").strip() == name for a in accounts.values()):
            return RedirectResponse("/dev/rag?" + urlencode({"err": f"「{name}」 부서 계정이 있어 지울 수 없습니다 — 사용자·계정에서 부서를 먼저 바꿉니다"})
                                    + "#dept-rags", status_code=303)
        new, moved = rag_spaces.delete_dept_rag(db, cfg, name)
        rag_spaces.save_config(new, data_dir)
        return RedirectResponse("/dev/rag?" + urlencode({"ok": f"「{name}」 부서 RAG 를 지웠습니다 · 문서 {moved}건은 공통 RAG 로"})
                                + "#dept-rags", status_code=303)
    return RedirectResponse("/dev/rag?" + urlencode({"err": "알 수 없는 요청"}) + "#dept-rags", status_code=303)

# 규정·기준 문서 — 반입한 규정을 골라 기준 문서로 올리고(조문 조각), 학생 공개를 정한다(regulation_register)
@router.get("/dev/rag/regs")
def dev_rag_regs(request: Request, q: str = ""):
    from zzaimy.app import regulation_register
    st = request.app.state
    got = regulation_register.candidates(st.db, q)
    return {**got, "reindex_needed": (st.data_dir / ".reindex-needed").exists(), "reindex_running": st.reindex_running()}

async def _ids_from(request: Request) -> list[int]:
    body = await request.json()
    return [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:500]

@router.post("/dev/rag/regs/register")
async def dev_rag_regs_register(request: Request):
    from zzaimy.app import regulation_register
    st = request.app.state
    return regulation_register.register(st.db, await _ids_from(request), st.data_dir, by=request.state.user or "")

@router.post("/dev/rag/regs/unregister")
async def dev_rag_regs_unregister(request: Request):
    from zzaimy.app import regulation_register
    st = request.app.state
    return regulation_register.unregister(st.db, await _ids_from(request), st.data_dir, by=request.state.user or "")

@router.post("/dev/rag/regs/audience")
async def dev_rag_regs_audience(request: Request):
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

@router.get("/dev/rag/try")
def dev_rag_try(request: Request, target: str = "", q: str = "", k: int = 5):
    """검색 범위 시험 — 공간·계정의 실제 검색 범위(search_scope)로 규정·사업 문서를 찾은 상위 결과. 생성 모델은 부르지 않는다."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    got = rag_spaces.target_scope(target, st.accounts, st.data_dir)
    if got is None:
        raise HTTPException(400, "공간이나 계정을 고르세요")
    term = (q or "").strip()[:200]
    if not term:
        raise HTTPException(400, "검색어를 넣으세요")
    label, sc = got
    res = rag_spaces.preview_search(st.db, sc, term, k=max(1, min(int(k), 10)))
    return {"label": label, "space": sc["space"], "uses_grant": bool(sc.get("grant")), "grant_depts": sc.get("grant_depts"),
            "student_only": sc.get("levels") == ("student",), **res}

@router.get("/dev/rag/space")
def dev_rag_space(request: Request, target: str = ""):
    """공간(또는 계정)의 문서 구성 — 사업별·갈래별 사업 문서 수, 규정 색인 문서별 조각 수 상위."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    got = rag_spaces.target_scope(target, st.accounts, st.data_dir)
    if got is None:
        raise HTTPException(400, "공간이나 계정을 고르세요")
    return {"label": got[0], **rag_spaces.composition(st.db, got[1])}
