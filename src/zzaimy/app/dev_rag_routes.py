"""RAG 공간 관리(/dev/rag*, ADR-0053) — 공간별 자료 수·쓰는 계정, 계정별 추가 권한, 부서 RAG, 규정·기준 문서, 검색 범위 시험.

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·데이터 폴더(data_dir)·계정(accounts)·화면 틀·화면 공통 맥락(page_ctx)과 다른 화면과 함께 쓰는 도우미(부서 목록 all_depts·
  재색인 진행 여부 reindex_running)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from zzaimy.app import grades

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
        "dept_rags": rag_spaces.dept_rag_rows(db, cfg, accounts, st.all_depts()),
        "staff_grades": {k: v for k, v in grades.GRADES.items() if k != "member"},
        "names": {u: ((a or {}).get("name") or "").strip() or u for u, a in accounts.items()}}))   # 아이디 대신 이름


@router.post("/dev/rag/min-grade")
async def dev_rag_min_grade(request: Request):
    """RAG 공간의 최소 등급(ADR-0066) — space=common | dept:<부서>, grade=빈 값(걸지 않음)·lead·head.
    걸면 그 등급 아래 교직원은 그 RAG 의 사업 문서를 검색 범위에서 빼고, 부서 RAG 면 열람에서도 뺀다(rag_spaces.search_scope·
    access_policy.viewer)."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    form = await request.form()
    space, grade = str(form.get("space") or "").strip(), str(form.get("grade") or "").strip()
    if grade and grade not in grades.GRADES:
        return RedirectResponse("/dev/rag?" + urlencode({"err": "등급 값이 올바르지 않습니다"}) + "#dept-rags", status_code=303)
    if space != grades.COMMON_SPACE and not (space.startswith("dept:") and space[5:] in st.all_depts()):
        return RedirectResponse("/dev/rag?" + urlencode({"err": "없는 RAG 입니다"}) + "#dept-rags", status_code=303)
    cfg = dict(rag_spaces.config(st.data_dir))
    mg = {k: v for k, v in (cfg.get("min_grade") or {}).items() if v}
    if grade:
        mg[space] = grade
    else:
        mg.pop(space, None)
    if mg:
        cfg["min_grade"] = mg
    else:
        cfg.pop("min_grade", None)
    rag_spaces.save_config(cfg, st.data_dir)
    name = "공통 RAG" if space == grades.COMMON_SPACE else f"{space[5:]} 부서 RAG"
    msg = f"{name} 최소 등급 — {grades.GRADES[grade] + ' 이상' if grade else '걸지 않음'}"
    return RedirectResponse("/dev/rag?" + urlencode({"ok": msg}) + "#dept-rags", status_code=303)

# 계정별 추가 권한(겸직 부서)은 사용자·계정 편집 창 한 곳에서 저장한다(user_admin — 같은 rag grants). 이 화면의 표·저장 경로는 뺐다(10-26)


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
    from zzaimy.app.doc_title import display_title     # 화면에는 확장자 뗀 제목, 파일 이름은 filename 에 그대로
    for d in got["registered"]:
        d["title"] = display_title({"filename": d.get("filename") or ""}) or d.get("filename") or ""
    for d in got["items"]:
        d["filename"] = d.get("title") or ""
        d["title"] = display_title({"filename": d["filename"]}) or d["filename"]
    from zzaimy.app import reindex_state
    return {**got, "reindex_needed": reindex_state.needed(st.db), "reindex_running": st.reindex_running()}

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

@router.get("/dev/rag/targets")
def dev_rag_targets(request: Request, q: str = "", limit: int = 30):
    """검색 범위 시험의 대상 찾기 — 사람 고르기 부품(people-picker)이 읽는 꼴로 공간과 계정을 함께 돌려준다.
    값은 「space:<공간>」·「user:<계정>」(dev_rag_try 가 받는 그대로), 보이는 이름은 공간 이름·사람 이름."""
    from zzaimy.app import rag_spaces
    from zzaimy.app.access_guard import ROLES
    st = request.app.state
    accounts = st.accounts or {}
    cfg = rag_spaces.config(st.data_dir)
    words = [w for w in (q or "").lower().split() if w]
    hit = lambda *t: not words or all(w in " ".join(t).lower() for w in words)
    out = []
    spaces = [("student", "student", None), ("staff", "staff", None), ("dev", "dev", None)] + \
             [(f"dept:{d}", "staff", d) for d in rag_spaces.known_depts(cfg, accounts)]
    for sid, role, dept in spaces:
        label = "관리자 — 모든 문서" if sid == "dev" else rag_spaces.resolve(role, dept, st.data_dir)["label"]
        if hit(sid, label):
            out.append({"user": f"space:{sid}", "name": label, "dept": "검색 공간", "grade_label": ""})
    for u, a in sorted(accounts.items()):
        name = (a.get("name") or "").strip() or u
        role = a.get("role", "staff")
        g = grades.label(grades.grade_of(a)) if role in grades.STAFF else ""
        who = " · ".join(x for x in (ROLES.get(role, role), g) if x)
        if hit(u, name, a.get("dept") or "", who):
            out.append({"user": f"user:{u}", "name": name, "dept": (a.get("dept") or "").strip() or "부서 없음", "grade_label": who})
    return {"people": out[:max(1, min(int(limit or 30), 80))], "depts": [], "my_dept": ""}


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
    if target.startswith("user:"):                     # 아이디 대신 사람 이름
        acct = st.accounts.get(target[5:]) or {}
        label = f"{(acct.get('name') or '').strip() or target[5:]} 계정"
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
