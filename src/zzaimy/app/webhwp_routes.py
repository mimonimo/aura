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


MINE = "~"          # 「내 폴더」 뿌리 이름의 앞 글자 — 부서 이름은 이 글자로 시작하지 않아 부서 디렉터리와 겹치지 않는다


def _mine(request: Request) -> str:
    """이 사람의 「내 폴더」 뿌리 키(부서 디렉터리 자리에 「~아이디」). 처음 열 때 저절로 생긴다."""
    from zzaimy.app import storage
    user = getattr(request.state, "user", "") or ""
    return storage.safe_name(MINE + user) if user else ""


def _depts(request: Request) -> list[str]:
    """이 사람이 쓰는 부서 디렉터리 + 자기 「내 폴더」. 관리자는 남의 「내 폴더」 도 열 수 있다(왼쪽 목록에는 안 보인다)."""
    from zzaimy.app.user_admin import known_depts
    st = request.app.state
    role = getattr(request.state, "role", "")
    user = getattr(request.state, "user", "")
    also = tuple(_ap.extra_depts(user, st.data_dir)) if role in _ap.STAFF_ROLES else ()
    known = known_depts(st.db, getattr(st, "accounts", {}) or {}, st.data_dir) if role == "dev" else ()
    out = [d for d in webhwp.depts_for(role, getattr(request.state, "dept", ""), also, known) if not d.startswith(MINE)]
    if out and _mine(request):
        out.append(_mine(request))
    if role == "dev":
        r = webhwp.root(_base(request))
        if r.is_dir():
            out += sorted(c.name for c in r.iterdir() if c.is_dir() and c.name.startswith(MINE) and c.name not in out)
    return out


def _label(request: Request, dept: str) -> str:
    """화면에 보일 뿌리 이름 — 내 「내 폴더」 는 「내 폴더」, 관리자가 보는 남의 것은 「아이디 님 폴더」."""
    if dept and dept == _mine(request):
        return "내 폴더"
    if dept.startswith(MINE):
        return f"{dept[len(MINE):]} 님 폴더"
    return dept


def _may_make(request: Request, dept: str) -> bool:
    """새 폴더·폴더 이름 바꾸기를 해도 되는 곳 — 내 부서(그 아래)와 「내 폴더」. 공통(공통 양식·양식)은 관리자만."""
    return dept in _depts(request) and (dept != webhwp.COMMON or getattr(request.state, "role", "") == "dev")


def _need_staff(request: Request) -> None:
    if getattr(request.state, "role", "") not in ("dev", *_ap.STAFF_ROLES):
        raise HTTPException(403, "교직원·관리자만 쓸 수 있습니다")


def _writable(request: Request, fid: int) -> None:
    """이 문서를 고쳐도(저장·이름 바꾸기·지우기) 되는가 — 잠긴 공통 양식은 관리자만,
    다른 사람이 편집 잠금을 잡고 있으면 409(편집 화면의 잠금만 믿지 않고 서버에서도 막는다)."""
    st = request.app.state
    if webhwp.locked(st.db, fid) and getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "공통 양식은 관리자만 고칠 수 있습니다 — 「사본 만들기」로 내 부서 폴더에 사본을 만들어 쓰세요")
    holder = webhwp.lock_holder(st.db, fid).get("user", "")
    if holder and holder != getattr(request.state, "user", ""):
        name = webhwp.display_name(st.db, holder, getattr(st, "accounts", None))
        raise HTTPException(409, f"{name} 님이 편집 중이라 저장하지 않았습니다")


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
        raise HTTPException(403, "이 부서 폴더는 쓸 수 없습니다")
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


# 왼쪽 「즐겨찾기」·「최근 폴더」 — 사람마다 설정 키 하나(JSON 목록 [{dept, folder}]). 보여 줄 때마다 지금 쓸 수 있는 부서·있는 폴더만 남긴다
_FAV_MAX = 30
_RECENT_MAX = 8
_RECENT_SHOWN = 5


def _place_key(kind: str, user: str) -> str:
    return f"hwp_{kind}:{user}"


def _places(request: Request, kind: str) -> list[dict]:
    import json as _json
    user = getattr(request.state, "user", "")
    if not user:
        return []
    try:
        raw = _json.loads(request.app.state.db.get_setting(_place_key(kind, user), "[]") or "[]")
    except ValueError:
        return []
    out = []
    for x in raw if isinstance(raw, list) else []:
        if isinstance(x, dict) and isinstance(x.get("dept"), str) and isinstance(x.get("folder"), str):
            out.append({"dept": x["dept"], "folder": x["folder"]})
    return out


def _put_places(request: Request, kind: str, items: list[dict]) -> None:
    import json as _json
    user = getattr(request.state, "user", "")
    if user:
        request.app.state.db.set_setting(_place_key(kind, user), _json.dumps(items, ensure_ascii=False))


def _place_ok(request: Request, depts: list[str], dept: str, folder: str) -> bool:
    """이 사람이 지금 쓸 수 있는 부서의, 디스크에 있는 폴더인가."""
    if dept not in depts:
        return False
    try:
        return webhwp.folder_dir(_base(request), dept, folder).is_dir()
    except ValueError:
        return False


def _place_view(request: Request, depts: list[str], items: list[dict]) -> list[dict]:
    out = []
    for x in items:
        if not _place_ok(request, depts, x["dept"], x["folder"]):
            continue
        segs = [s for s in x["folder"].split("/") if s]
        lab = _label(request, x["dept"])
        out.append({"dept": x["dept"], "label": lab, "folder": x["folder"], "name": segs[-1] if segs else lab,
                    "where": " / ".join([lab, *segs[:-1]]) if segs else "", "href": _where(x["dept"], x["folder"])})
    return out


def _touch(request: Request, dept: str, folder: str) -> None:
    """최근 폴더에 올린다 — 연 폴더·문서를 만들거나 고친 폴더. 부서 맨 위는 왼쪽 부서 목록에 이미 있어 넣지 않는다."""
    if not dept or not folder:
        return
    try:
        items = [x for x in _places(request, "recent") if (x["dept"], x["folder"]) != (dept, folder)]
        _put_places(request, "recent", [{"dept": dept, "folder": folder}, *items][:_RECENT_MAX])
    except Exception:                                                   # 기록 실패로 화면·저장이 멈추면 안 된다
        import logging
        logging.getLogger("zzaimy.app.webhwp").exception("최근 폴더 기록 실패")


def _side_places(request: Request, depts: list[str], here: dict | None) -> tuple[list[dict], list[dict]]:
    favs = _place_view(request, depts, _places(request, "fav"))
    fav_keys = {(x["dept"], x["folder"]) for x in favs}
    cur = (here["dept"], here["folder"]) if here else None
    recent = [x for x in _place_view(request, depts, _places(request, "recent"))
              if (x["dept"], x["folder"]) not in fav_keys and (x["dept"], x["folder"]) != cur][:_RECENT_SHOWN]
    return favs, recent


@router.get("/hwp", response_class=HTMLResponse)
def hwp_home(request: Request, dept: str = "", folder: str = "", ok: str = "", err: str = ""):
    """문서함 — 왼쪽 위치(즐겨찾기·최근 폴더·부서 폴더·구글 드라이브), 오른쪽 고른 곳의 폴더·문서(폴더 만들기·올리기·새 문서·지우기).
    한글 파일은 늘 웹 한글 기안기로 연다 — 편집기 고르기(/hwp/editor)는 다른 화면의 설정이라 여기서는 묻지 않는다."""
    _need_staff(request)
    st = request.app.state
    _ensure_templates(st.db, _base(request))
    depts = _depts(request)
    mine = getattr(request.state, "dept", "") or webhwp.COMMON
    dept = dept if dept in depts else (mine if mine in depts else (depts[0] if depts else ""))
    folder = _check_dept(request, dept, folder) if dept else ""
    here = webhwp.browse(st.db, _base(request), dept, folder) if dept else None
    if here:
        here["label"] = _label(request, dept)
        here["may_make"] = _may_make(request, dept)
        from zzaimy.app import past_forms
        here["sorts"] = past_forms.sorts_here(dept, folder)
    if here and _place_ok(request, depts, dept, folder):
        _touch(request, dept, folder)
    favs, recent = _side_places(request, depts, here)
    mine = _mine(request)
    return st.templates.TemplateResponse(request, "hwp.html", st.ctx(request, {
        "depts": webhwp.listing(st.db, _base(request), [d for d in depts if not d.startswith(MINE)]), "here": here,
        "mine": mine if mine in depts else "",
        "favorites": favs, "recent": recent, "fav_keys": {(x["dept"], x["folder"]) for x in favs}, "copy_at": _copy_at(request),
        "status": webhwp.status(), "ok": ok, "err": err}))


@router.post("/hwp/favorite")
def hwp_favorite(request: Request, dept: str = Form(""), folder: str = Form(""), on: str = Form("1")):
    """즐겨찾기 켜기·끄기 — 내 것만(사람마다 설정 키). 켤 때는 지금 쓸 수 있는 부서의 있는 폴더만 받는다.
    화면 스크립트는 JSON(목록 다시 그리기), 스크립트 없이 보낸 폼은 그 폴더로 돌아간다."""
    _need_staff(request)
    try:
        folder = webhwp.clean_folder(folder)
    except ValueError as e:
        raise HTTPException(400, str(e))
    depts = _depts(request)
    want = on not in ("0", "", "false", "off")
    items = [x for x in _places(request, "fav") if (x["dept"], x["folder"]) != (dept, folder)]
    if want:
        if not _place_ok(request, depts, dept, folder):
            raise HTTPException(403, "이 폴더는 즐겨찾기에 넣을 수 없습니다")
        if len(items) >= _FAV_MAX:
            raise HTTPException(400, f"즐겨찾기는 {_FAV_MAX}개까지입니다")
        items.append({"dept": dept, "folder": folder})
    _put_places(request, "fav", items)
    if "application/json" in (request.headers.get("accept") or ""):
        return {"ok": True, "on": want, "favorites": _place_view(request, depts, items)}
    return RedirectResponse(_where(dept, folder) if dept in depts else "/hwp", status_code=303)


def _folder_name(name: str) -> str:
    """새 폴더·이름 바꾸기 이름 — 경로 문자·숨김(점으로 시작)·너무 긴 이름은 받지 않는다."""
    n = (name or "").strip()
    if not n:
        raise ValueError("폴더 이름을 적어 주세요")
    if any(c in n for c in '/\\:*?"<>|') or n.startswith(".") or n in webhwp.HIDDEN:
        raise ValueError("폴더 이름에 / \\ : * ? \" < > | 와 앞쪽 점은 쓸 수 없습니다")
    if len(n) > 60:
        raise ValueError("폴더 이름은 60자까지입니다")
    return n


def _json_wanted(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


@router.post("/hwp/folder")
def hwp_folder(request: Request, dept: str = Form(""), folder: str = Form(""), name: str = Form("")):
    """새 폴더 — 내 부서·내 폴더 안에서만(공통은 관리자만). 같은 이름이 있으면 거절. 화면 스크립트는 JSON, 폼·파일 창은 돌아가기."""
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    if not _may_make(request, dept):
        raise HTTPException(403, "여기에는 폴더를 만들 수 없습니다 — 내 부서 폴더나 내 폴더에서 만드세요")
    try:
        n = _folder_name(name)
        if webhwp.folder_dir(_base(request), dept, f"{folder}/{n}").exists():
            raise ValueError(f"「{n}」 폴더가 이미 있습니다")
        sub = webhwp.make_folder(_base(request), dept, folder, n)
    except ValueError as e:
        if _json_wanted(request):
            raise HTTPException(400, str(e))
        return _back(str(e), ok=False, dept=dept, folder=folder)
    _touch(request, dept, sub)
    if _json_wanted(request):
        return {"ok": True, "name": sub.split("/")[-1], "path": sub, "href": _where(dept, sub)}
    return _back(f"「{sub.split('/')[-1]}」 폴더를 만들었습니다", dept=dept, folder=sub)


@router.post("/hwp/folder-rename")
def hwp_folder_rename(request: Request, dept: str = Form(""), folder: str = Form(""), name: str = Form("")):
    """폴더 이름 바꾸기(JSON) — 새 폴더와 같은 곳·같은 이름 규칙. 안의 문서 장부 경로와 내 즐겨찾기·최근 폴더도 함께 고친다.
    안의 문서를 다른 사람이 편집 중이면 409."""
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    if not folder or not _may_make(request, dept):
        raise HTTPException(403, "이 폴더는 이름을 바꿀 수 없습니다")
    base = _base(request)
    old = webhwp.folder_dir(base, dept, folder)
    if not old.is_dir():
        raise HTTPException(404, "폴더가 없습니다")
    try:
        n = _folder_name(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    parent = "/".join(folder.split("/")[:-1])
    new_rel = webhwp.clean_folder(f"{parent}/{n}")
    if new_rel == folder:
        return {"ok": True, "name": n, "path": folder, "href": _where(dept, folder)}
    new = webhwp.folder_dir(base, dept, new_rel)
    if new.exists():
        raise HTTPException(400, f"「{n}」 폴더가 이미 있습니다")
    st, me = request.app.state, getattr(request.state, "user", "")
    prefix = str(old.resolve()) + "/"
    for r in st.db.list_files(kind="webhwp", limit=8000):
        if str(Path(r["path"]).resolve()).startswith(prefix):
            who = webhwp.lock_holder(st.db, r["id"]).get("user", "")
            if who and who != me:
                who_name = webhwp.display_name(st.db, who, getattr(st, "accounts", None))
                raise HTTPException(409, f"「{r['name']}」 을(를) {who_name} 님이 편집 중이라 이름을 바꾸지 않았습니다")
    old.rename(new)
    st.db.repath_files(str(old) + "/", str(new) + "/")
    if str(old.resolve()) != str(old):
        st.db.repath_files(prefix, str(new.resolve()) + "/")
    for kind in ("fav", "recent"):
        items = []
        for x in _places(request, kind):
            f_ = x["folder"]
            if x["dept"] == dept and (f_ == folder or f_.startswith(folder + "/")):
                x = {"dept": dept, "folder": new_rel + f_[len(folder):]}
            items.append(x)
        _put_places(request, kind, items)
    return {"ok": True, "name": n, "path": new_rel, "href": _where(dept, new_rel)}


@router.post("/hwp/{fid}/presence")
async def hwp_presence(request: Request, fid: int):
    """같은 문서를 연 사람 표시 — 편집 화면이 커서 칸이 바뀔 때·몇 초마다 알린다(공동 편집 표, 바뀌면 알림 흐름으로 다른 화면에)."""
    _need_staff(request)
    _file(request, fid)
    from starlette.concurrency import run_in_threadpool
    from zzaimy.app import webhwp_coedit as ce
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    user = getattr(request.state, "user", "")
    client = str(body.get("client") or f"{user}:page")[:80]
    db = request.app.state.db
    await run_in_threadpool(ce.presence, db, fid, user, client, str(body.get("field") or "")[:200])
    # 지금 같이 있는 사람을 함께 돌려준다 — 알림은 칸이 바뀔 때만 가서, 가만히 있는 사람이 30초 뒤 남의 화면에서 사라졌다(10-24)
    people = await run_in_threadpool(ce.people, db, fid)
    return {"ok": True, "people": [{"user": p["user"], "client": p["client"], "field": p["field"],
                                    "name": request.app.state.person_name(p["user"])} for p in people]}


@router.post("/hwp/{fid}/live")
async def hwp_live(request: Request, fid: int):
    """(예전 편집 화면) 칸 글 올리기·받기 — 이제 공동 편집 표 위에서. 새 화면은 /ops·/events 를 쓴다."""
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
    field = str(body.get("field") or "")[:200]
    if field and isinstance(body.get("text"), str):
        webhwp.live_put(st.db, fid, user, field, body["text"])
    v, changes = webhwp.live_since(st.db, fid, int(body.get("since") or 0), user)
    names = {}
    for c in changes:
        names.setdefault(c["by"], webhwp.display_name(st.db, c["by"], getattr(st, "accounts", None)))
        c["name"] = names[c["by"]]
    return {"v": v, "changes": changes}


@router.get("/hwp/{fid}/state")
def hwp_state(request: Request, fid: int):
    """문서 상태 — 마지막 저장(누가), 에이전트 작성 중(AURA 커서), 같이 보는 사람, 구조 잠금."""
    _need_staff(request)
    f = _file(request, fid)
    from zzaimy.app import webhwp_agent
    from zzaimy.app import webhwp_coedit as ce
    st = request.app.state
    m = webhwp.meta(st.db, fid)
    me = getattr(request.state, "user", "")
    people = [{"user": p["user"], "name": webhwp.display_name(st.db, p["user"], getattr(st, "accounts", None)), "field": p["field"]}
              for p in ce.people(st.db, fid) if p["user"] != me]
    return {"saved": m.get("saved") or m.get("created") or "", "saved_by": m.get("saved_by", ""), "size": f.get("size", 0),
            "aura": webhwp_agent.activity(st.db, fid), "lock": webhwp.lock_holder(st.db, fid), "people": people}


@router.post("/hwp/{fid}/lock")
def hwp_lock(request: Request, fid: int):
    """편집 잠금 잡기·늘리기(편집 화면이 45초마다) — 다른 사람이 편집 중이면 ok=false 와 그 사람."""
    _need_staff(request)
    _file(request, fid)
    if webhwp.locked(request.app.state.db, fid) and getattr(request.state, "role", "") != "dev":
        return {"ok": False, "by": "", "readonly": True}
    r = webhwp.take_lock(request.app.state.db, fid, getattr(request.state, "user", ""))
    r["by_name"] = webhwp.display_name(request.app.state.db, r["by"], getattr(request.app.state, "accounts", None))     # 화면에는 계정 아이디가 아닌 이름
    return r


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


def _dept_drives(request: Request) -> list[dict]:
    """왼쪽 「<부서> 드라이브」 — 내가 속한 부서(소속·추가 부서)의 연결된 부서 드라이브 폴더. 그 폴더는 부서 사람의 학교 계정에
    편집 권한이 주어져 있어(ingest/dept_drive 맞추기) 내 계정으로 연다 — 부서 계정의 다른 파일은 건드리지 않는다."""
    from zzaimy.ingest import dept_drive
    st = request.app.state
    user = getattr(request.state, "user", "")
    out = []
    for d in _depts(request):
        if d == webhwp.COMMON or d.startswith(MINE):
            continue
        try:
            stt = dept_drive.state(st.db, d, getattr(st, "accounts", {}) or {}, st.data_dir)
        except Exception:
            continue
        if stt.get("connected") and stt.get("folder") and (user in stt.get("members", []) or getattr(request.state, "role", "") == "dev"):
            out.append({"dept": d, "name": f"{d} 드라이브", "folder": stt["folder"]})
    return out


def _shared_with_me(email: str) -> dict:
    """공유 문서함(나와 공유됨) 맨 위 — 폴더와 한글 파일."""
    from zzaimy.app import webhwp_drive
    from zzaimy.ingest import gdrive, gdrive_files
    http = webhwp_drive._http()
    r = http.get(f"{gdrive.API}/files", headers=gdrive_files._headers(email, http),
                 params={"q": "sharedWithMe = true and trashed = false", "pageSize": 200, "orderBy": "folder,name",
                         "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
                         "fields": "files(id,name,mimeType,modifiedTime,size)"})
    if r.status_code != 200:
        raise RuntimeError(f"공유 문서함을 읽지 못했습니다({r.status_code})")
    items = r.json().get("files") or []
    return {"folders": [{"id": f["id"], "name": f["name"]} for f in items if f.get("mimeType") == webhwp_drive.FOLDER_MIME],
            "files": [f for f in items if webhwp_drive.accepts(f, "hwp")]}


@router.get("/api/hwp/drive/browse")
def hwp_drive_browse(request: Request, folder: str = "", drive: str = "", q: str = "", shared: int = 0):
    """문서함 왼쪽 구글 드라이브 — 드라이브 폴더의 하위 폴더·한글 파일. 맨 위를 물으면 공유 드라이브와 내 부서 드라이브 목록도.
    shared=1 이면 공유 문서함(나와 공유됨) 맨 위."""
    _need_staff(request)
    from zzaimy.app import webhwp_drive
    email = _google(request)
    try:
        out = _shared_with_me(email) if shared else webhwp_drive.browse(email, folder, drive, q)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    if not folder and not drive and not q and not shared:
        out["drives"] = webhwp_drive.drives(email)
        out["dept_drives"] = _dept_drives(request)
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
    _touch(request, dept, folder)
    return RedirectResponse(f"{EDIT_PAGE}?id={fid}", status_code=303)


@router.post("/hwp/{fid}/to-drive")
def hwp_to_drive(request: Request, fid: int):
    """지금 저장된 판을 드라이브에 최종본으로 올린다."""
    _need_staff(request)
    f = _file(request, fid)
    from zzaimy.app import webhwp_drive, webhwp_coedit
    email = _google(request)
    webhwp_coedit.fresh(request.app.state.db, _base(request), fid)
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
    if here:
        here["label"], here["may_make"] = _label(request, dept), _may_make(request, dept)
    mine = _mine(request)
    shown = [d for d in depts if not d.startswith(MINE) or d == mine]       # 관리자에게도 남의 「내 폴더」 는 목록에 띄우지 않는다
    return {"depts": [{**x, "label": _label(request, x["dept"]), "may_make": _may_make(request, x["dept"])}
                      for x in webhwp.listing(st.db, _base(request), shown)], "here": here}


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


def _copy_at(request: Request) -> dict:
    """복사해 쓰기 창이 처음 여는 곳 — 내 부서(공통 말고)에서 마지막으로 연·고친 폴더, 없으면 그 부서 맨 위.
    쓸 수 있는 내 부서가 없으면 빈 곳(창에서 고르게 한다 — 공통으로 몰래 넣지 않는다)."""
    depts = _depts(request)
    own = [d for d in depts if d != webhwp.COMMON]
    mine = getattr(request.state, "dept", "")
    dept = mine if mine in own else (own[0] if own else "")
    if not dept:
        return {"dept": "", "folder": ""}
    for x in _places(request, "recent"):
        if x["dept"] == dept and _place_ok(request, depts, dept, x["folder"]):
            return {"dept": dept, "folder": x["folder"]}
    return {"dept": dept, "folder": ""}


def _copy_name(src: Path, name: str) -> str:
    """복사본 이름 — 고른 이름에 원본 확장자를 붙인다(경로 문자 걸러내기는 webhwp.add 가). 비었으면 원본 이름에서 「 공통 양식」 을 뺀 것."""
    stem = (name or "").strip()
    if stem.lower().endswith((".hwpx", ".hwp")):
        stem = stem.rsplit(".", 1)[0].strip()
    stem = stem.replace("/", " ").replace("\\", " ")[:80].strip()
    return f"{stem}{src.suffix.lower()}" if stem else src.name.replace(" 공통 양식", "")


def _work_dept(request: Request) -> str:
    """프로젝트 작업본을 둘 부서 — 내 소속 부서(공통·내 폴더 말고). 없으면 빈 문자열."""
    depts = [d for d in _depts(request) if d != webhwp.COMMON and not d.startswith(MINE)]
    mine = getattr(request.state, "dept", "")
    return mine if mine in depts else (depts[0] if depts else "")


def _project_folder(proj: dict) -> str:
    """프로젝트의 한글 작업 폴더 — 프로젝트 화면(project_routes._hwp_work_docs)이 그 프로젝트 문서로 싣는 「작업/<프로젝트 이름>」."""
    from zzaimy.app import storage
    return webhwp.clean_folder(f"작업/{storage.safe_name(proj.get('name') or '')[:40]}")


def _writable_projects(request: Request) -> list[dict]:
    """복사본을 넣을 수 있는 프로젝트 — 진행 중이고 내가 소유·함께 작업(쓰기)인 것. 참조(읽기 전용) 공유는 뺀다."""
    from zzaimy.app import project_access as pa
    st = request.app.state
    acc = getattr(st, "project_access", None)
    if acc is None:
        return []
    with st.db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM projects WHERE archived = 0 ORDER BY id DESC LIMIT 400").fetchall()]
    return [p for p in rows if pa.can_write(acc(request, p))]


@router.get("/api/hwp/projects")
def hwp_projects(request: Request):
    """복사해 쓰기 창의 「프로젝트」 목록 — 쓸 수 있는 프로젝트만(이름·id)."""
    _need_staff(request)
    if not _work_dept(request):
        return {"projects": []}
    return {"projects": [{"id": int(p["id"]), "name": p.get("name") or ""} for p in _writable_projects(request)][:60]}


@router.post("/hwp/{fid}/copy")
def hwp_copy(request: Request, fid: int, dept: str = Form(""), folder: str = Form(""), name: str = Form(""),
             project: str = Form("")):
    """복사해 쓰기 — 공통 양식(또는 볼 수 있는 문서)을 고른 곳에 고른 이름으로 복사하고 기안기로 연다.
    곳은 화면의 위치 고르기 창에서 받는다: 부서 폴더·내 폴더, 또는 프로젝트(project=id → 내 부서의 「작업/<프로젝트 이름>」,
    프로젝트 화면이 그 폴더를 프로젝트 작업 문서로 싣는다 — 소유·함께 작업만, 참조는 403).
    부서를 안 주면 내 부서(공통 말고) — 그것도 없으면 고르라고 돌려보낸다(공통으로 몰래 넣지 않는다)."""
    _need_staff(request)
    f = _file(request, fid)
    wants_json = _json_wanted(request)
    if project.strip():
        try:
            pid = int(project)
        except ValueError:
            raise HTTPException(400, "프로젝트 번호가 올바르지 않습니다")
        proj = next((p for p in _writable_projects(request) if int(p["id"]) == pid), None)
        if proj is None:
            raise HTTPException(403, "이 프로젝트에는 복사할 수 없습니다 — 소유하거나 「함께 작업」으로 공유받은 프로젝트만 됩니다")
        dept = _work_dept(request)
        if not dept:
            raise HTTPException(400, "소속 부서가 있어야 프로젝트 작업본을 만들 수 있습니다")
        folder = _project_folder(proj)
    elif not dept:
        dept = _copy_at(request)["dept"]
        if not dept:
            msg = "복사할 부서 폴더를 골라 주세요"
            if wants_json:
                raise HTTPException(400, msg)
            return _back(msg, ok=False)
    folder = _check_dept(request, dept, folder)
    p = Path(f["path"])
    new = webhwp.add(request.app.state.db, _base(request), dept, _copy_name(p, name), p.read_bytes(),
                     owner=getattr(request.state, "user", ""), source=f"복사:{fid}", folder=folder)
    _touch(request, dept, folder)
    if wants_json:
        return {"ok": True, "id": new, "url": f"{EDIT_PAGE}?id={new}"}
    return RedirectResponse(f"{EDIT_PAGE}?id={new}", status_code=303)


@router.post("/hwp/delete-many")
async def hwp_delete_many(request: Request):
    """여러 문서 지우기(JSON {ids}) — 하나씩 지우기와 같은 검사(볼 수 있는 부서, 잠긴 양식은 관리자만, 남이 편집 중이면 안 됨).
    된 것과 안 된 것(까닭)을 따로 돌려준다."""
    _need_staff(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "지울 문서를 골라 주세요")
    if len(ids) > 500:
        raise HTTPException(400, "한 번에 500개까지 지울 수 있습니다")
    done, rejected = [], []
    for x in ids:
        try:
            fid = int(x)
            f = _file(request, fid)
            _writable(request, fid)
        except (TypeError, ValueError):
            rejected.append({"id": x, "name": "", "why": "없는 문서입니다"})
            continue
        except HTTPException as e:
            rejected.append({"id": x, "name": "", "why": str(e.detail).replace("저장하지 않았습니다", "지우지 않았습니다")})
            continue
        webhwp.trash(request.app.state.db, _base(request), fid, user=getattr(request.state, "user", ""))
        done.append(fid)
    return {"ok": True, "deleted": done, "rejected": rejected}


@router.post("/hwp/{fid}/delete")
def hwp_delete(request: Request, fid: int):
    """지우기 — 부서 휴지통으로 옮긴다(관리자가 되살릴 수 있다). 화면 스크립트는 JSON(그 줄만 빼기), 폼은 문서함으로 돌아간다."""
    _need_staff(request)
    f = _file(request, fid)
    wants_json = "application/json" in (request.headers.get("accept") or "")
    dept, folder = webhwp.dept_of_path(_base(request), f["path"]), webhwp.folder_of(_base(request), f["path"])
    try:
        _writable(request, fid)
    except HTTPException as e:
        if e.status_code != 409:
            raise
        msg = str(e.detail).replace("저장하지 않았습니다", "지우지 않았습니다")
        if wants_json:
            raise HTTPException(409, msg)
        return _back(msg, ok=False, dept=dept, folder=folder)
    webhwp.trash(request.app.state.db, _base(request), fid, user=getattr(request.state, "user", ""))
    msg = f"「{f['name']}」 을(를) 휴지통으로 옮겼습니다"
    if wants_json:
        return {"ok": True, "message": msg}
    return _back(msg, dept=dept, folder=folder)


@router.post("/hwp/editor")
def hwp_editor(request: Request, editor: str = Form("gdocs"), back: str = Form("/hwp")):
    _need_staff(request)
    webhwp.set_editor(request.app.state.db, getattr(request.state, "user", ""), editor)
    back = back if back.startswith("/") and not back.startswith("//") else "/hwp"
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}ok={_q('문서 편집기를 ' + webhwp.EDITORS.get(editor, '') + '(으)로 정했습니다')}", status_code=303)


@router.post("/hwp/upload")
async def hwp_upload(request: Request, dept: str = Form(""), folder: str = Form(""), file: UploadFile = File(...)):
    """올리기 — 공통의 「지난 사업 문서」·「공통 양식」 에서는 past_forms 판정으로 제자리(잠긴 양식)에 넣거나 까닭을 들어 거절하고,
    그 밖의 곳은 고른 폴더에 그대로 둔다. 화면 스크립트(Accept JSON)에는 JSON 으로 답한다."""
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        if _json_wanted(request):
            return {"ok": False, "message": "100MB 보다 큰 파일은 올릴 수 없습니다"}
        return _back("100MB 보다 큰 파일은 올릴 수 없습니다", ok=False, dept=dept, folder=folder)
    from zzaimy.app import past_forms
    if past_forms.sorts_here(dept, folder):
        from starlette.concurrency import run_in_threadpool
        res = await run_in_threadpool(_take_past_form, request, file.filename or "", data)
        if _json_wanted(request):
            return res
        return _back(res["message"], ok=res["ok"], dept=dept, folder=folder)
    try:
        webhwp.add(request.app.state.db, _base(request), dept, file.filename or "", data,
                   owner=getattr(request.state, "user", ""), source="upload", folder=folder)
    except ValueError as e:
        return _back(str(e), ok=False, dept=dept, folder=folder)
    _touch(request, dept, folder)
    return _back(f"「{file.filename}」 을(를) 올렸습니다", dept=dept, folder=folder)


def _programs(request: Request):
    """사업 장부(past_forms.Programs) — 올릴 때마다 지금 장부로."""
    from zzaimy.app import past_forms
    return past_forms.Programs(request.app.state.db, request.app.state.data_dir)


def _take_past_form(request: Request, filename: str, data: bytes) -> dict:
    from zzaimy.app import past_forms
    return past_forms.take_upload(request.app.state.db, _base(request), filename, data,
                                  owner=getattr(request.state, "user", ""), programs=_programs(request))


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
    ctx["copy_at"] = _copy_at(request) if ctx["readonly"] else {"dept": "", "folder": ""}   # 「복사해 쓰기」 창이 처음 여는 곳
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
    _touch(request, dept, folder)
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
    from zzaimy.app import webhwp_coedit
    webhwp_coedit.fresh(request.app.state.db, _base(request), fid)       # 공동 편집 칸 글을 먼저 파일에 — 여는 판이 최신
    f = webhwp.get(request.app.state.db, fid) or f
    p = Path(f["path"])
    if not p.exists():
        raise HTTPException(404, "파일이 없습니다")
    return Response(p.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_q(p.name)}", "Cache-Control": "no-store"})


@router.post("/hwp/{fid}/save")
async def hwp_save(request: Request, fid: int):
    """기안기에서 받은 HWPX 를 그대로(본문 바이트) 저장 — 앞 판은 .판/ 으로."""
    _need_staff(request)
    f = _file(request, fid)
    _writable(request, fid)
    data = await _body(request)
    from starlette.concurrency import run_in_threadpool
    from zzaimy.app import webhwp_coedit as ce
    db, user = request.app.state.db, getattr(request.state, "user", "")

    def run():
        # 칸 글은 공동 편집 표가 정답 — 이 판에 표의 칸 글을 덮어 써 저장한다(다른 사람이 동시에 쓴 칸이 사라지지 않게)
        with ce.file_lock(fid):
            new, done, versions = ce.overlay(db, fid, data)
            p_ = webhwp.save(db, _base(request), fid, new, user=user)
            ce._mark_committed(db, fid, versions)
        return p_, new, done
    try:
        p, data, merged = await run_in_threadpool(run)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _touch(request, webhwp.dept_of_path(_base(request), f["path"]), webhwp.folder_of(_base(request), f["path"]))
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
