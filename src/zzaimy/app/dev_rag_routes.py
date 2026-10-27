"""RAG 공간 관리(/dev/rag*, ADR-0053) — 공간별 자료 수·쓰는 계정, 부서 RAG 와 다른 부서에 열기, 검색 범위 시험.
기준 문서(규정) 관리는 라이브러리 「기준」 칩(criteria_regs_routes)으로 옮겼다(10-27).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·데이터 폴더(data_dir)·계정(accounts)·화면 틀·화면 공통 맥락(page_ctx)과 다른 화면과 함께 쓰는 도우미(부서 목록 all_depts·
  재색인 진행 여부 reindex_running)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from zzaimy.app import grades

router = APIRouter()


# RAG 공간 관리(ADR-0053) — 공간별 자료 수·쓰는 계정, 부서 RAG 열어 주기, 폴더→부서 짝. 개발자만(/dev 미들웨어)

@router.get("/dev/rag", response_class=HTMLResponse)
def dev_rag(request: Request):
    """RAG 공간 — 위에 공통·학생 RAG 요약, 부서 RAG 목록(찾기·문서 있는 것만 기본), 고른 RAG 의 상세(구성·최소 등급·쓰는 계정),
    끝에 검색 범위 시험·내려받기. 수치는 모두 이 요청에서 센다."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    db, accounts = st.db, st.accounts
    cfg = rag_spaces.config(st.data_dir)
    spaces = {s["id"]: s for s in rag_spaces.stats(db, st.data_dir, accounts, only=("staff", "student"))}
    names = {u: ((a or {}).get("name") or "").strip() or u for u, a in accounts.items()}   # 아이디 대신 이름
    staff_grades = {k: v for k, v in grades.GRADES.items() if k != "member"}
    mg = cfg.get("min_grade") or {}
    grade_label = lambda sid: (staff_grades.get(mg.get(sid, "")) or "") and staff_grades[mg[sid]] + " 이상"  # noqa: E731
    who = lambda ids: [names.get(u, u) for u in ids]  # noqa: E731
    common, stu = spaces.get("staff") or {}, spaces.get("student") or {}
    rows = [{"sid": "common", "comp": "space:staff", "kind": "common", "name": "공통 RAG", "docs": common.get("grant_docs", 0),
             "reg_docs": common.get("reg_docs", 0), "reg_chunks": common.get("reg_chunks", 0),
             "members": who(common.get("users", [])), "granted": [], "lend_to": [], "lend_from": [], "folders": [], "min_grade": mg.get("common", ""),
             "min_label": grade_label("common"), "lib": "/?type=all&scope=" + quote("공통")},
            {"sid": "student", "comp": "space:student", "kind": "student", "name": "학생 RAG", "docs": None,
             "reg_docs": stu.get("reg_docs", 0), "reg_chunks": stu.get("reg_chunks", 0),
             "members": who(stu.get("users", [])), "granted": [], "lend_to": [], "lend_from": [], "folders": [], "min_grade": "", "min_label": "", "lib": ""}]
    for r in rag_spaces.dept_rag_rows(db, cfg, accounts, st.all_depts()):
        sid = "dept:" + r["dept"]
        rows.append({"sid": sid, "comp": "space:" + sid, "kind": "dept", "name": r["dept"], "docs": r["docs"],
                     "reg_docs": r["reg_docs"], "reg_chunks": r["reg_chunks"], "members": who(r["members"]),
                     "granted": who(r["granted"]), "lend_to": r["lend_to"], "lend_from": r["lend_from"], "folders": r["folders"], "made_at": (r.get("made") or {}).get("at", ""),
                     "min_grade": mg.get(sid, ""), "min_label": grade_label(sid), "lib": "/?type=all&scope=" + quote(r["dept"])})
    depts = [r for r in rows if r["kind"] == "dept"]
    return st.templates.TemplateResponse(request, "dev_rag.html", st.page_ctx(request, {
        "rag_rows": rows, "rag_common": rows[0], "rag_student": rows[1], "depts": depts,
        "dept_filled": [r for r in depts if r["docs"] or r["reg_docs"]],
        "dept_empty": [r for r in depts if not (r["docs"] or r["reg_docs"])],
        # 빈 부서는 많을 때만 한 줄로 접는다 — 몇 개뿐이면 그냥 보인다(3개를 접어 두면 표가 빈 머리만 남았다)
        "fold_empty": sum(1 for r in depts if not (r["docs"] or r["reg_docs"])) > 8,
        "cfg": cfg, "staff_grades": staff_grades}))


@router.post("/dev/rag/min-grade")
async def dev_rag_min_grade(request: Request):
    """RAG 공간의 최소 등급(ADR-0066) — space=common | dept:<부서>, grade=빈 값(걸지 않음)·lead·head.
    걸면 그 등급 아래 교직원은 그 RAG 의 사업 문서를 검색 범위에서 빼고, 부서 RAG 면 열람에서도 뺀다(rag_spaces.search_scope·
    access_policy.viewer)."""
    from zzaimy.app import rag_spaces
    from zzaimy.app.dept_drive_routes import wants_json
    st = request.app.state
    form = await request.form()
    as_json = wants_json(request)                          # 조직 관리의 부서 설정은 fetch 로 보낸다 — {ok, msg}
    space, grade = str(form.get("space") or "").strip(), str(form.get("grade") or "").strip()
    wants_json = "application/json" in (request.headers.get("accept") or "")    # 화면은 제자리에서 바꾼다(다시 불러오지 않음)

    def fail(msg: str):
        if wants_json:
            return JSONResponse({"error": msg}, status_code=400)
        return RedirectResponse("/dev/rag?" + urlencode({"err": msg}) + "#dept-rags", status_code=303)
    if grade and grade not in grades.GRADES:
        return fail("등급 값이 올바르지 않습니다")
    if space != grades.COMMON_SPACE and not (space.startswith("dept:") and space[5:] in st.all_depts()):
        return fail("없는 RAG 입니다")
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
    if wants_json:
        return {"ok": msg, "space": space, "grade": grade, "label": grades.GRADES[grade] + " 이상" if grade else ""}
    return RedirectResponse("/dev/rag?" + urlencode({"ok": msg}) + "#dept-rags", status_code=303)

# 다른 부서에 열기(dept_grants)는 부서 단위 — 저장은 조직 관리와 같은 POST /org/rag-lend(관리자·그 부서 총괄 책임자, org_routes).
# 겸직 부서는 계정의 부서별 등급(dept_grades) 한 곳 — 사용자·계정 편집 창에서 정한다


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
    else:                                              # 공간 id 대신 화면의 RAG 이름
        sid = target[6:]
        label = {"staff": "공통 RAG", "student": "학생 RAG", "dev": "관리자 범위"}.get(sid) or (f"{sid[5:]} 부서 RAG" if sid.startswith("dept:") else label)
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
