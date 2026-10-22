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


@router.post("/hwp/{fid}/presence")
async def hwp_presence(request: Request, fid: int):
    """같은 문서를 연 사람 표시 — 편집 화면이 몇 초마다 지금 있는 필드를 알린다(이름표: 이름 · 필드)."""
    _need_staff(request)
    _file(request, fid)
    import json as _json
    import time as _time
    try:
        body = await request.json()
    except Exception:
        body = {}
    st = request.app.state
    user = getattr(request.state, "user", "")
    try:
        cur = _json.loads(st.db.get_setting(f"webhwp_view:{int(fid)}", "{}") or "{}")
    except ValueError:
        cur = {}
    now = _time.time()
    cur = {u: v for u, v in cur.items() if now - float(v.get("at") or 0) < 30}
    cur[user] = {"at": now, "field": str(body.get("field") or "")[:60]}
    st.db.set_setting(f"webhwp_view:{int(fid)}", _json.dumps(cur, ensure_ascii=False))
    return {"ok": True}


@router.post("/hwp/{fid}/live")
async def hwp_live(request: Request, fid: int):
    """칸 단위 실시간 공유 — 내 커서 필드의 글을 올리고(field·text), since 뒤에 다른 사람이 바꾼 필드 글을 받는다."""
    _need_staff(request)
    _file(request, fid)
    if webhwp.locked(request.app.state.db, fid) and getattr(request.state, "role", "") != "dev":
        return {"v": 0, "changes": []}
    try:
        body = await request.json()
    except Exception:
        body = {}
    st = request.app.state
    user = getattr(request.state, "user", "")
    field = str(body.get("field") or "")[:80]
    if field and isinstance(body.get("text"), str):
        webhwp.live_put(st.db, fid, user, field, body["text"])
    v, changes = webhwp.live_since(st.db, fid, int(body.get("since") or 0), user)
    names = {}
    for c in changes:
        names.setdefault(c["by"], (st.db.profile_for(c["by"]) or {}).get("name") or c["by"])
        c["name"] = names[c["by"]]
    return {"v": v, "changes": changes}


@router.get("/hwp/{fid}/state")
def hwp_state(request: Request, fid: int):
    """편집 화면이 몇 초마다 묻는 문서 상태 — 마지막 저장(누가), 에이전트 작성 중(AURA 커서), 같이 보는 사람."""
    _need_staff(request)
    f = _file(request, fid)
    import json as _json
    import time as _time
    from zzaimy.app import webhwp_agent
    st = request.app.state
    m = webhwp.meta(st.db, fid)
    try:
        view = _json.loads(st.db.get_setting(f"webhwp_view:{int(fid)}", "{}") or "{}")
    except ValueError:
        view = {}
    now = _time.time()
    me = getattr(request.state, "user", "")
    names = {u: (st.db.profile_for(u) or {}).get("name") or u for u in view}
    people = [{"user": u, "name": names[u], "field": v.get("field", "")} for u, v in view.items()
              if u != me and now - float(v.get("at") or 0) < 30]
    return {"saved": m.get("saved") or m.get("created") or "", "saved_by": m.get("saved_by", ""), "size": f.get("size", 0),
            "aura": webhwp_agent.activity(st.db, fid), "lock": webhwp.lock_holder(st.db, fid), "people": people}


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


def _google(request: Request) -> str:
    """이 사람이 연결한 학교 구글 계정 — 없으면 409(설정에서 연결하라고)."""
    from zzaimy.ingest import gdrive_files
    st = request.app.state
    user = getattr(request.state, "user", "")
    email = gdrive_files.account_for(st.db, user, getattr(request.state, "dept", "") or None)
    if not email:
        raise HTTPException(409, "학교 구글 계정이 연결되어 있지 않습니다 — 설정에서 「내 학교 계정 연결」 뒤 다시 해 주세요")
    return email


@router.get("/api/hwp/drive")
def hwp_drive_list(request: Request, q: str = "", page: str = ""):
    """내 드라이브의 한글 파일 목록(가져오기 창)."""
    _need_staff(request)
    from zzaimy.app import webhwp_drive
    try:
        return {"account": (email := _google(request)), **webhwp_drive.list_files(email, q, page)}
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/api/hwp/drive/browse")
def hwp_drive_browse(request: Request, folder: str = "", drive: str = "", q: str = ""):
    """파일 가져오기 창(파인더 꼴) — 드라이브 폴더의 하위 폴더·한글 파일, 공유 드라이브 목록."""
    _need_staff(request)
    from zzaimy.app import webhwp_drive
    email = _google(request)
    try:
        out = webhwp_drive.browse(email, folder, drive, q)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    if not folder and not drive and not q:
        out["drives"] = webhwp_drive.drives(email)
    return {"account": email, **out}


@router.post("/hwp/drive/import")
def hwp_drive_import(request: Request, file_id: str = Form(...), dept: str = Form(""), folder: str = Form("")):
    """드라이브 → 부서 디렉터리로 받아 기안기로 연다."""
    _need_staff(request)
    from zzaimy.app import webhwp_drive
    folder = _check_dept(request, dept, folder)
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_-]{10,128}", file_id.strip()):          # 드라이브 파일 id 꼴만 — 주소 조각을 끼워 넣지 못하게
        raise HTTPException(400, "드라이브 파일 id 가 올바르지 않습니다")
    email = _google(request)
    try:
        fid = webhwp_drive.import_file(request.app.state.db, _base(request), email, file_id.strip(), dept=dept, folder=folder,
                                       owner=getattr(request.state, "user", ""))
    except (RuntimeError, ValueError) as e:
        return _back(str(e), ok=False, dept=dept, folder=folder)
    return RedirectResponse(f"{EDIT_PAGE}?id={fid}", status_code=303)


@router.post("/hwp/{fid}/to-drive")
def hwp_to_drive(request: Request, fid: int):
    """지금 저장된 판을 드라이브에 최종본으로 올린다."""
    _need_staff(request)
    f = _file(request, fid)
    from zzaimy.app import webhwp_drive
    email = _google(request)
    try:
        up = webhwp_drive.upload_final(request.app.state.db, fid, email, dept=webhwp.dept_of_path(_base(request), f["path"]))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "url": up["url"], "name": up["name"]}


@router.get("/api/hwp/browse")
def hwp_browse_api(request: Request, dept: str = "", folder: str = ""):
    """문서 패널의 부서 폴더 — 쓸 수 있는 부서 목록과 고른 부서·폴더의 하위 폴더·한글 파일(JSON)."""
    _need_staff(request)
    st = request.app.state
    depts = _depts(request)
    mine = getattr(request.state, "dept", "") or webhwp.COMMON
    dept = dept if dept in depts else (mine if mine in depts else (depts[0] if depts else ""))
    folder = _check_dept(request, dept, folder) if dept else ""
    here = webhwp.browse(st.db, _base(request), dept, folder) if dept else None
    return {"depts": webhwp.listing(st.db, _base(request), depts), "here": here}


@router.get("/api/hwp/templates")
def hwp_templates(request: Request):
    """양식으로 새 문서 — 한글 문서함의 잠긴 양식(공통 양식·공문·학교 양식) 목록을 묶음별로."""
    _need_staff(request)
    st = request.app.state
    _ensure_templates(st.db, _base(request))
    groups: dict[str, list[dict]] = {}
    for r in st.db.list_files(kind="webhwp", limit=8000):
        if not webhwp.locked(st.db, r["id"]) or not Path(r["path"]).exists():
            continue
        folder = webhwp.folder_of(_base(request), r["path"]) or "기타"
        groups.setdefault(folder, []).append({"id": r["id"], "name": Path(r["name"]).stem})
    order = sorted(groups, key=lambda g: (not g.startswith("공통 양식/공문"), not g.startswith("공통 양식"), g))
    return {"groups": [{"group": g, "items": sorted(groups[g], key=lambda x: x["name"])} for g in order]}


@router.post("/doc/{doc_id}/webhwp")
def doc_to_webhwp(request: Request, doc_id: int, dept: str = Form(""), folder: str = Form("")):
    """사업 양식으로 작업 — 문서함·프로젝트의 한글 원본(hwp·hwpx)을 hwpx 로 바꾸고 쓸 자리 필드를 박아 부서 디렉터리에 작업본으로.
    열람 권한은 앱 인증 단계(/doc/{id} 경로 검사)가 이미 확인했다."""
    _need_staff(request)
    from zzaimy.app import storage, webhwp_convert
    st = request.app.state
    doc = st.db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    ext = storage.original_ext(doc)
    if ext not in (".hwp", ".hwpx"):
        raise HTTPException(400, "한글 파일(hwp·hwpx)만 기안기로 작업할 수 있습니다")
    mine = getattr(request.state, "dept", "") or webhwp.COMMON
    dept = dept or (mine if mine in _depts(request) else webhwp.COMMON)
    project = st.db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
    folder = _check_dept(request, dept, folder or (f"작업/{storage.safe_name(project['name'])[:40]}" if project else "작업"))
    try:
        data = _original_bytes(st, doc)
        work, fields = webhwp_convert.work_form(data, ext)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        return RedirectResponse(f"/doc/{doc_id}?err=webhwp&msg={_q(str(e)[:120])}", status_code=303)
    title = storage.title_of(doc.get("filename") or "") or "양식"
    fid = webhwp.add(st.db, _base(request), dept, f"{title} 작업본.hwpx", work, owner=getattr(request.state, "user", ""),
                     source=f"doc:{doc_id}", folder=folder)
    m = webhwp.meta(st.db, fid)
    m["fields"] = [f["name"] for f in fields][:400]
    import json as _json
    st.db.set_setting(f"webhwp_meta:{fid}", _json.dumps(m, ensure_ascii=False))
    return RedirectResponse(f"{EDIT_PAGE}?id={fid}", status_code=303)


def _original_bytes(st, doc: dict) -> bytes:
    """문서 원본 바이트 — 플랫폼에 있으면 그것, DGX 원본(dgx://)이면 원본 보관소에서 하나 받아 온다."""
    sp = str(doc.get("stored_path") or "")
    if sp and not sp.startswith("dgx://") and Path(sp).exists():
        return Path(sp).read_bytes()
    if sp.startswith("dgx://"):
        from zzaimy.app import archive, archive_original
        archive.ensure(st.db)
        with st.db._conn() as conn:
            row = conn.execute("SELECT rel, size, mtime FROM archive_files WHERE rel = ? AND removed_at = ''", (sp[6:],)).fetchone()
        if row:
            p = archive_original.fetch(dict(zip(("rel", "size", "mtime"), row)))
            try:
                return Path(p).read_bytes()
            finally:
                Path(p).unlink(missing_ok=True)
    raise FileNotFoundError("원본 파일을 찾을 수 없습니다")


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
def hwp_edit_page(request: Request, id: int | None = None, dept: str = "", folder: str = "", embed: int = 0):
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
    ctx["embed"] = bool(embed)                              # 채팅 문서 창 안 — 「문서함」 돌아가기는 빼고 새 창 열기는 창 머리줄에
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
    data = await _body(request)
    # 동시 편집 — 다른 사람이 칸(필드)에 올린 최신 글을 이 판에 합쳐 저장한다(서로 덮어쓰지 않게)
    data, merged = webhwp.merge_live(request.app.state.db, fid, data, getattr(request.state, "user", ""))
    try:
        p = webhwp.save(request.app.state.db, _base(request), fid, data, user=getattr(request.state, "user", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "name": p.name, "size": len(data), "merged": merged}


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
