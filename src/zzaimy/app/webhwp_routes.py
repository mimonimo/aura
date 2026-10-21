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


async def _body(request: Request, limit: int | None = None) -> bytes:
    """본문을 한도까지만 읽는다 — 큰 본문을 다 받아 놓고 재지 않게(Content-Length 를 먼저 보고, 흘려 받으며 센다)."""
    limit = MAX_BYTES if limit is None else limit
    try:
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, "파일이 너무 큽니다")
    except ValueError:
        raise HTTPException(400, "Content-Length 가 올바르지 않습니다")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, "파일이 너무 큽니다")
    return bytes(buf)


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


def _writable(request: Request, fid: int) -> None:
    if webhwp.locked(request.app.state.db, fid) and getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "공통 양식은 관리자만 고칠 수 있습니다 — 「복사해 쓰기」로 내 부서 폴더에 복사해 쓰세요")


def _file(request: Request, fid: int) -> dict:
    f = webhwp.get(request.app.state.db, fid)
    if not webhwp.may_open(_base(request), f, _depts(request)):
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    return f


def _where(dept: str = "", folder: str = "") -> str:
    q = []
    if dept:
        q.append(f"dept={_q(dept)}")
    if folder:
        q.append(f"folder={_q(folder)}")
    return "/hwp" + ("?" + "&".join(q) if q else "")


def _back(msg: str, ok: bool = True, dept: str = "", folder: str = "") -> RedirectResponse:
    url = _where(dept, folder)
    return RedirectResponse(f"{url}{'&' if '?' in url else '?'}{'ok' if ok else 'err'}={_q(msg)}", status_code=303)


def _check_dept(request: Request, dept: str, folder: str = "") -> str:
    if dept not in _depts(request):
        raise HTTPException(403, "이 부서 디렉터리는 쓸 수 없습니다")
    try:
        return webhwp.clean_folder(folder)
    except ValueError as e:
        raise HTTPException(400, str(e))


_TEMPLATES_READY: set[str] = set()


def _ensure_templates(db, base: Path) -> None:
    """한글 공통 양식(공통/공통 양식)을 프로세스마다 한 번 맞춘다 — 정의가 바뀐 것만 새로 만든다."""
    key = str(base)
    if key in _TEMPLATES_READY:
        return
    _TEMPLATES_READY.add(key)
    try:
        from zzaimy.app import webhwp_templates
        webhwp_templates.ensure_all(db, base)
    except Exception:
        import logging
        logging.getLogger("zzaimy.app.webhwp").exception("한글 공통 양식 만들기 실패")


@router.get("/hwp", response_class=HTMLResponse)
def hwp_home(request: Request, dept: str = "", folder: str = "", ok: str = "", err: str = ""):
    """부서 한글 문서함 — 왼쪽 부서 목록, 오른쪽 고른 부서의 폴더·문서(폴더 만들기·올리기·새 문서·지우기), 편집기 고르기."""
    _need_staff(request)
    st = request.app.state
    _ensure_templates(st.db, _base(request))
    depts = _depts(request)
    mine = getattr(request.state, "dept", "") or webhwp.COMMON
    dept = dept if dept in depts else (mine if mine in depts else (depts[0] if depts else ""))
    folder = _check_dept(request, dept, folder) if dept else ""
    return st.templates.TemplateResponse(request, "hwp.html", st.ctx(request, {
        "depts": webhwp.listing(st.db, _base(request), depts), "here": webhwp.browse(st.db, _base(request), dept, folder) if dept else None,
        "status": webhwp.status(), "editor": webhwp.editor_of(st.db, getattr(request.state, "user", "")),
        "editors": webhwp.EDITORS, "ok": ok, "err": err}))


@router.post("/hwp/folder")
def hwp_folder(request: Request, dept: str = Form(""), folder: str = Form(""), name: str = Form("")):
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    try:
        sub = webhwp.make_folder(_base(request), dept, folder, name)
    except ValueError as e:
        return _back(str(e), ok=False, dept=dept, folder=folder)
    return _back(f"「{sub.split('/')[-1]}」 폴더를 만들었습니다", dept=dept, folder=sub)


@router.post("/hwp/{fid}/lock")
def hwp_lock(request: Request, fid: int):
    """편집 잠금 잡기·늘리기(편집 화면이 45초마다) — 다른 사람이 편집 중이면 ok=false 와 그 사람."""
    _need_staff(request)
    _file(request, fid)
    if webhwp.locked(request.app.state.db, fid) and getattr(request.state, "role", "") != "dev":
        return {"ok": False, "by": "", "readonly": True}
    return webhwp.take_lock(request.app.state.db, fid, getattr(request.state, "user", ""))


@router.post("/hwp/{fid}/unlock")
def hwp_unlock(request: Request, fid: int):
    _need_staff(request)
    _file(request, fid)
    webhwp.release_lock(request.app.state.db, fid, getattr(request.state, "user", ""))
    return {"ok": True}


@router.post("/hwp/{fid}/copy")
def hwp_copy(request: Request, fid: int, dept: str = Form(""), folder: str = Form("")):
    """복사해 쓰기 — 공통 양식(또는 볼 수 있는 문서)을 내 부서 디렉터리(폴더)에 복사하고 기안기로 연다."""
    _need_staff(request)
    f = _file(request, fid)
    mine = getattr(request.state, "dept", "") or webhwp.COMMON
    dept = dept or (mine if mine in _depts(request) else webhwp.COMMON)
    folder = _check_dept(request, dept, folder)
    p = Path(f["path"])
    name = p.name.replace(" 공통 양식", "")
    new = webhwp.add(request.app.state.db, _base(request), dept, name, p.read_bytes(),
                     owner=getattr(request.state, "user", ""), source=f"복사:{fid}", folder=folder)
    return RedirectResponse(f"{EDIT_PAGE}?id={new}", status_code=303)


@router.post("/hwp/{fid}/delete")
def hwp_delete(request: Request, fid: int):
    """지우기 — 부서 휴지통으로 옮긴다(관리자가 되살릴 수 있다)."""
    _need_staff(request)
    f = _file(request, fid)
    _writable(request, fid)
    dept, folder = webhwp.dept_of_path(_base(request), f["path"]), webhwp.folder_of(_base(request), f["path"])
    webhwp.trash(request.app.state.db, _base(request), fid, user=getattr(request.state, "user", ""))
    return _back(f"「{f['name']}」 을(를) 휴지통으로 옮겼습니다", dept=dept, folder=folder)


@router.post("/hwp/editor")
def hwp_editor(request: Request, editor: str = Form("gdocs"), back: str = Form("/hwp")):
    _need_staff(request)
    webhwp.set_editor(request.app.state.db, getattr(request.state, "user", ""), editor)
    back = back if back.startswith("/") and not back.startswith("//") else "/hwp"
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}ok={_q('문서 편집기를 ' + webhwp.EDITORS.get(editor, '') + '(으)로 정했습니다')}", status_code=303)


@router.post("/hwp/upload")
async def hwp_upload(request: Request, dept: str = Form(""), folder: str = Form(""), file: UploadFile = File(...)):
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return _back("100MB 보다 큰 파일은 올릴 수 없습니다", ok=False, dept=dept, folder=folder)
    try:
        webhwp.add(request.app.state.db, _base(request), dept, file.filename or "", data,
                   owner=getattr(request.state, "user", ""), source="upload", folder=folder)
    except ValueError as e:
        return _back(str(e), ok=False, dept=dept, folder=folder)
    return _back(f"「{file.filename}」 을(를) 올렸습니다", dept=dept, folder=folder)


EDIT_PAGE = "/webhwpctrl/aura-edit"     # 기안기 스크립트는 「지금 페이지의 폴더」 + hwpctrlmain.html 로 편집 틀을 연다 — 그래서 /webhwpctrl/ 바로 아래


@router.get("/hwp/new")
def hwp_new(request: Request, dept: str = "", folder: str = ""):
    """빈 문서로 기안기 열기 — 처음 저장할 때 부서 디렉터리(그 폴더)에 파일이 생긴다."""
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    return RedirectResponse(f"{EDIT_PAGE}?dept={_q(dept)}" + (f"&folder={_q(folder)}" if folder else ""), status_code=303)


@router.get(EDIT_PAGE, response_class=HTMLResponse)
def hwp_edit_page(request: Request, id: int | None = None, dept: str = "", folder: str = ""):
    """기안기 편집 화면(전체 화면, 사이드바 없이) — id 가 있으면 그 문서, 없으면 dept 부서(folder 폴더)의 새 문서."""
    _need_staff(request)
    st = request.app.state
    if id is not None:
        f = _file(request, id)
        ctx = {"f": f, "dept": webhwp.dept_of_path(_base(request), f["path"]),
               "folder": webhwp.folder_of(_base(request), f["path"]), "meta": webhwp.meta(st.db, id),
               "readonly": webhwp.locked(st.db, id) and getattr(request.state, "role", "") != "dev"}
    else:
        folder = _check_dept(request, dept, folder)
        ctx = {"f": None, "dept": dept, "folder": folder, "meta": {}, "readonly": False}
    ctx["back"] = _where(ctx["dept"], ctx["folder"])
    return st.templates.TemplateResponse(request, "hwp_edit.html", {"request": request, **ctx, "status": webhwp.status()})


@router.post("/hwp/new")
async def hwp_new_save(request: Request, dept: str = "", title: str = "", folder: str = ""):
    """새 문서 첫 저장 — 본문 바이트(HWPX)를 부서 디렉터리에 들이고 id 를 돌려준다."""
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    data = await _body(request)
    if data[:2] != b"PK":
        raise HTTPException(400, "한글(HWPX) 파일이 아닙니다")
    fid = webhwp.add(request.app.state.db, _base(request), dept, f"{(title or '새 문서').strip()[:60]}.hwpx", data,
                     owner=getattr(request.state, "user", ""), source="new", folder=folder)
    return {"ok": True, "id": fid, "url": f"{EDIT_PAGE}?id={fid}"}


@router.get("/hwp/{fid}/edit")
def hwp_edit(request: Request, fid: int):
    _need_staff(request)
    _file(request, fid)
    return RedirectResponse(f"{EDIT_PAGE}?id={int(fid)}", status_code=303)


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
    _writable(request, fid)
    held = webhwp.lock_holder(request.app.state.db, fid)
    if held and held["user"] != getattr(request.state, "user", ""):
        raise HTTPException(409, f"{held['user']} 님이 편집 중이라 저장하지 않았습니다 — 끝난 뒤 다시 열어 고쳐 주세요")
    data = await _body(request)
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
    from urllib.parse import quote, unquote
    # 기안기 앱(/webhwpctrl/) 밖으로 나가지 못하게 — ../ 나 인코딩한 ../, 역슬래시로 관리 화면·다른 앱에 닿는 길을 막는다
    raw = unquote(path)
    if raw.startswith("/") or "\\" in raw or any(seg in ("..", ".") for seg in raw.split("/")):
        raise HTTPException(400, "올바르지 않은 경로입니다")
    url = f"{webhwp.base_url()}/webhwpctrl/{quote(raw, safe='/-._~')}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    # 플랫폼 로그인 쿠키(zz_session)는 기안기 서버에 넘기지 않는다 — 기안기 자기 세션 쿠키만
    jar = "; ".join(f"{k}={v}" for k, v in request.cookies.items() if k != "zz_session")
    if jar:
        headers["cookie"] = jar
    body = await _body(request)
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
