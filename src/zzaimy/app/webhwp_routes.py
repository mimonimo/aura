"""웹 한글 기안기 화면·프록시 — 부서 문서함(/hwp), 기안기로 열기(/hwp/{id}/edit), 저장, 편집기 고르기, 기안기 서버 프록시(/webhwpctrl/).

기안기 서버(Tomcat)는 127.0.0.1 에만 열려 있다. 브라우저는 플랫폼 주소의 /webhwpctrl/ 로 들어오고 이 프록시가 그대로 넘긴다 —
로그인 검사는 앱의 인증 단계가 먼저 한다(학생은 허용 목록 밖이라 저절로 막힌다). 규칙·저장 자리는 app/webhwp.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote as _q

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from zzaimy.app import access_policy as _ap
from zzaimy.app import webhwp

router = APIRouter()
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
        "upgrade", "host", "content-length", "content-encoding", "cookie"}
MAX_BYTES = 100 * 1024 * 1024


def _base(request: Request) -> Path:
    return Path(request.app.state.data_dir)


def _depts(request: Request) -> list[str]:
    from zzaimy.app.user_admin import known_depts
    st = request.app.state
    role = getattr(request.state, "role", "")
    user = getattr(request.state, "user", "")
    also = tuple(_ap.extra_depts(user, st.data_dir)) if role in _ap.STAFF_ROLES else ()
    known = known_depts(st.db, getattr(st, "accounts", {}) or {}, st.data_dir) if role == "dev" else ()
    return webhwp.depts_for(role, getattr(request.state, "dept", ""), also, known)


def _need_staff(request: Request) -> None:
    if getattr(request.state, "role", "") not in ("dev", *_ap.STAFF_ROLES):
        raise HTTPException(403, "교직원·관리자만 쓸 수 있습니다")


def _file(request: Request, fid: int) -> dict:
    f = webhwp.get(request.app.state.db, fid)
    if not webhwp.may_open(_base(request), f, _depts(request)):
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    return f


def _back(msg: str, ok: bool = True, dept: str = "") -> RedirectResponse:
    return RedirectResponse(f"/hwp?{'ok' if ok else 'err'}={_q(msg)}" + (f"#d-{_q(dept)}" if dept else ""), status_code=303)


@router.get("/hwp", response_class=HTMLResponse)
def hwp_home(request: Request, ok: str = "", err: str = ""):
    """부서 한글 문서함 — 내가 쓰는 부서 디렉터리의 한글 파일과 올리기, 편집기 고르기."""
    _need_staff(request)
    st = request.app.state
    depts = _depts(request)
    return st.templates.TemplateResponse(request, "hwp.html", st.ctx(request, {
        "groups": webhwp.listing(st.db, _base(request), depts), "status": webhwp.status(),
        "editor": webhwp.editor_of(st.db, getattr(request.state, "user", "")), "editors": webhwp.EDITORS,
        "my_dept": getattr(request.state, "dept", "") or webhwp.COMMON, "ok": ok, "err": err}))


@router.post("/hwp/editor")
def hwp_editor(request: Request, editor: str = Form("gdocs"), back: str = Form("/hwp")):
    _need_staff(request)
    webhwp.set_editor(request.app.state.db, getattr(request.state, "user", ""), editor)
    back = back if back.startswith("/") and not back.startswith("//") else "/hwp"
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}ok={_q('문서 편집기를 ' + webhwp.EDITORS.get(editor, '') + '(으)로 정했습니다')}", status_code=303)


@router.post("/hwp/upload")
async def hwp_upload(request: Request, dept: str = Form(""), file: UploadFile = File(...)):
    _need_staff(request)
    if dept not in _depts(request):
        raise HTTPException(403, "이 부서 디렉터리에는 올릴 수 없습니다")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return _back("100MB 보다 큰 파일은 올릴 수 없습니다", ok=False, dept=dept)
    try:
        fid = webhwp.add(request.app.state.db, _base(request), dept, file.filename or "", data,
                         owner=getattr(request.state, "user", ""), source="upload")
    except ValueError as e:
        return _back(str(e), ok=False, dept=dept)
    return RedirectResponse(f"/hwp/{fid}/edit", status_code=303)


@router.get("/hwp/{fid}/edit", response_class=HTMLResponse)
def hwp_edit(request: Request, fid: int):
    _need_staff(request)
    f = _file(request, fid)
    st = request.app.state
    return st.templates.TemplateResponse(request, "hwp_edit.html", st.ctx(request, {
        "f": f, "dept": webhwp.dept_of_path(_base(request), f["path"]), "meta": webhwp.meta(st.db, fid),
        "status": webhwp.status()}))


@router.get("/hwp/{fid}/file")
def hwp_file(request: Request, fid: int):
    _need_staff(request)
    f = _file(request, fid)
    p = Path(f["path"])
    if not p.exists():
        raise HTTPException(404, "파일이 없습니다")
    return Response(p.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_q(p.name)}", "Cache-Control": "no-store"})


@router.post("/hwp/{fid}/save")
async def hwp_save(request: Request, fid: int):
    """기안기에서 받은 HWPX 를 그대로(본문 바이트) 저장 — 앞 판은 .판/ 으로."""
    _need_staff(request)
    _file(request, fid)
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "파일이 너무 큽니다")
    try:
        p = webhwp.save(request.app.state.db, _base(request), fid, data, user=getattr(request.state, "user", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "name": p.name, "size": len(data)}


@router.api_route("/webhwpctrl/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
async def webhwp_proxy(request: Request, path: str):
    """기안기 서버로 그대로 넘긴다 — 교직원·관리자만(인증은 앱 단계에서 이미)."""
    _need_staff(request)
    import httpx
    url = f"{webhwp.base_url()}/webhwpctrl/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    # 플랫폼 로그인 쿠키(zz_session)는 기안기 서버에 넘기지 않는다 — 기안기 자기 세션 쿠키만
    jar = "; ".join(f"{k}={v}" for k, v in request.cookies.items() if k != "zz_session")
    if jar:
        headers["cookie"] = jar
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.request(request.method, url, params=list(request.query_params.multi_items()),
                                     headers=headers, content=body)
    except httpx.HTTPError:
        raise HTTPException(503, "웹 한글 기안기 서버에 연결하지 못했습니다")
    out = {k: v for k, v in r.headers.items() if k.lower() not in _HOP and k.lower() != "set-cookie"}
    resp = Response(r.content, status_code=r.status_code, headers=out)
    for c in r.headers.get_list("set-cookie"):          # 기안기 세션 쿠키는 경로를 /webhwpctrl 로 두고 그대로 넘긴다
        resp.headers.append("set-cookie", c)
    return resp
