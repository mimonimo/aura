"""부서 드라이브 — 부서가 스스로 구글 드라이브를 연결해 함께 쓴다(10-23 사용자: 「각 부서에서 구글 드라이브 연결해서 공유해서
쓰는 걸로, 관리자는 연결된 계정 확인」). 폴더 만들기·권한 맞추기는 ingest/dept_drive.

- 연결: 그 부서의 총괄 책임자(상위 부서 총괄 책임자 포함)나 관리자가 「구글 드라이브 연결」 → 구글 계정 고르기 화면(prompt=
  select_account)에서 부서가 함께 쓸 학교 계정으로 로그인 → 되돌아오는 주소(/dev/gdrive/callback, 본인 연결과 같은 주소라 구글
  콘솔에 새로 넣을 것이 없다)가 finish_connect 로 넘긴다. state 는 무작위 값을 서버에만 두고(gdrive._pending_states, 10분)
  허용을 시작한 플랫폼 계정·「dept:<부서>」 목적과 함께 대조한다. 도메인 검사(학교 계정만)는 본인 연결과 같은 exchange_code.
  토큰은 사용자 토큰과 같은 보관소에 메일별로 두고, 부서와 그 메일은 부서 설정으로 잇는다. 토큰·코드는 기록하지 않는다.
- 사업 책임자·행정 담당자·일반은 상태만 본다. 학생·다른 부서는 볼 수 없다.
- 화면은 조직 관리(/team)의 부서 설정 한 곳 — 관리자도 그 자리에서 확인·권한 맞추기·연결 끊기를 한다.

경로: GET /team/drive?dept=…(JSON) · GET /team/drive/folders(위치 고르기) ·
POST /team/drive/connect · /team/drive/sync · /team/drive/disconnect · /team/drive/move(부서 폴더 위치 바꾸기)
(폼 그대로 보내면 back 으로 돌아가고, Accept: application/json 이면 {ok, msg}). 화면은 조직 관리 부서 설정 안의 상자(static/dept-drive.js 가 그린다).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from zzaimy.app import grades
from zzaimy.ingest import dept_drive, gdrive

router = APIRouter()
log = logging.getLogger(__name__)
_SAFE_BACK = re.compile(r"/(?![/\\])[^\s\\]*")


def wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


def _ctx(request: Request) -> tuple[str, dict, object]:
    st = request.app.state
    me = getattr(request.state, "user", "") or ""
    acct = {**((getattr(st, "accounts", None) or {}).get(me) or {}), "role": getattr(request.state, "role", "") or ""}
    return me, acct, getattr(st, "data_dir", None)


def _known(request: Request, dept: str) -> bool:
    from zzaimy.app.user_admin import known_depts
    st = request.app.state
    return bool(dept) and dept in known_depts(st.db, getattr(st, "accounts", None) or {}, getattr(st, "data_dir", None))


def may_manage(request: Request, dept: str) -> bool:
    """연결·다시 연결·끊기 — 관리자와 그 부서 총괄 책임자. 테스트 보기 중에는 하지 않는다(보이는 등급과 실제 권한이 다르다)."""
    if getattr(request.state, "view_as", None):
        return False
    me, acct, dd = _ctx(request)
    return dept_drive.can_manage(me, acct, dept, dd)


def may_view(request: Request, dept: str) -> bool:
    if may_manage(request, dept):
        return True
    me, acct, dd = _ctx(request)
    return acct.get("role") in grades.STAFF and dept in grades.depts_of(me, acct, dd)


def public_state(request: Request, dept: str) -> dict:
    """화면에 줄 상태 — 사람 아이디 목록 대신 수만(부서 사람 화면이 따로 있다)."""
    st = request.app.state
    s = dept_drive.state(st.db, dept, getattr(st, "accounts", None) or {}, getattr(st, "data_dir", None))
    return {"dept": dept, "connected": s["connected"], "shared": s["shared"], "personal": s["personal"], "legacy": s["legacy"],
            "account": s["by"], "folder": s["folder"], "folder_name": s["name"] or dept_drive.folder_name(dept),
            "granted": s["granted"], "members": len(s["members"]), "unlinked": len(s["unlinked"]),
            "actor": s["actor"], "connected_at": s["connected_at"], "error": s["error"],
            "can_manage": may_manage(request, dept), "app_ready": gdrive.configured(), "domain": gdrive.allowed_domain()}


def _done(request: Request, back: str, msg: str, ok: bool = True, dept: str = ""):
    if wants_json(request):
        body = {"ok": ok, "msg": msg}
        if dept:
            body["drive"] = public_state(request, dept)
        return JSONResponse(body, status_code=200 if ok else 400)
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}{'ok' if ok else 'err'}={quote(msg)}", status_code=303)


def _back_of(back: str, dept: str) -> str:
    """돌아갈 서비스 안 경로 — 쿠키·주소에 그대로 넣을 수 있게 퍼센트 인코딩한다(한글 부서 이름)."""
    back = quote(back or "", safe="/?=&%#-_.~")
    return back if _SAFE_BACK.fullmatch(back) else f"/team?dept={quote(dept)}"


@router.get("/team/drive")
def team_drive(request: Request, dept: str = ""):
    dept = " ".join(dept.split())
    if not _known(request, dept) or not may_view(request, dept):
        raise HTTPException(403, "이 부서의 드라이브 상태를 볼 수 없습니다")
    return JSONResponse(public_state(request, dept))


@router.post("/team/drive/connect")
def team_drive_connect(request: Request, dept: str = Form(""), back: str = Form("")):
    """구글 계정 고르기 화면으로 보낸다 — 거기서 부서가 함께 쓸 학교 계정으로 로그인한다."""
    dept = " ".join(dept.split())
    back = _back_of(back, dept)
    if not _known(request, dept) or not may_manage(request, dept):
        raise HTTPException(403, "부서 드라이브는 그 부서의 총괄 책임자와 관리자가 연결합니다")
    from zzaimy.app.user_admin import redirect_uri
    try:
        url = gdrive.auth_url(redirect_uri(request), getattr(request.state, "user", "") or "", purpose=f"dept:{dept}",
                              select_account=True)
    except ValueError as e:
        return _done(request, back, str(e), ok=False)
    if wants_json(request):
        resp = JSONResponse({"ok": True, "url": url})
    else:
        resp = RedirectResponse(url, status_code=303)
    resp.set_cookie("zz_google_back", back, max_age=900, httponly=True, samesite="lax")
    return resp


def finish_connect(request: Request, dept: str, code: str, state: str, uri: str) -> tuple[str, bool]:
    """되돌아온 허용으로 부서 드라이브를 잇는다 — (안내, 성공). 되돌아오는 주소는 /dev 검사 밖이라 권한을 여기서 다시 본다."""
    st = request.app.state
    db = st.db
    me = getattr(request.state, "user", "") or ""
    if not _known(request, dept) or not may_manage(request, dept):
        with gdrive._lock:                                  # 쓰지 못한 state 는 바로 버린다(한 번만 쓰는 값)
            gdrive._pending_states.pop(state or "", None)
        return "부서 드라이브는 그 부서의 총괄 책임자와 관리자가 연결합니다", False
    try:
        email = gdrive.exchange_code(code, state, uri, user=me, purpose=f"dept:{dept}")
    except ValueError as e:
        return str(e), False
    old = (db.get_setting(f"google_account_dept:{dept}", "") or "").strip()
    try:
        dept_drive.connect(db, dept, email, "root", shared=True, actor=me)
    except Exception as e:
        log.warning("부서 %s 드라이브 연결 실패: %s", dept, type(e).__name__)
        _drop_if_orphan(request, email)                     # 부서에 잇지 못한 토큰은 보관하지 않는다(아무도 안 쓰면 거둔다)
        return str(e)[:200], False
    if old and old != email:
        _drop_if_orphan(request, old)
    accounts = getattr(st, "accounts", None) or {}
    got = dept_drive.sync(db, dept, accounts, getattr(st, "data_dir", None))
    s = dept_drive.state(db, dept, accounts, getattr(st, "data_dir", None))
    msg = f"{dept} 부서 드라이브를 {email} 계정으로 연결했습니다 · 접근 {s['granted']}명"
    if s["unlinked"]:
        msg += f" · 구글 미연결 {len(s['unlinked'])}명"
    if got["error"]:
        msg += f" ({got['error']})"
    return msg, not got["error"]


def _drop_if_orphan(request: Request, email: str) -> None:
    from zzaimy.app.user_admin import _drop_token_if_orphan
    _drop_token_if_orphan(request.app.state.db, request, email)


def sync_now(request: Request, dept: str) -> tuple[str, bool]:
    st = request.app.state
    got = dept_drive.sync(st.db, dept, getattr(st, "accounts", None) or {}, getattr(st, "data_dir", None))
    msg = f"{dept} 부서 드라이브 권한을 맞췄습니다 · 줌 {got['added']} · 거둠 {got['removed']}"
    return msg + (f" ({got['error']})" if got["error"] else ""), not got["error"]


def disconnect_now(request: Request, dept: str) -> tuple[str, bool]:
    ok, email = dept_drive.disconnect(request.app.state.db, dept)
    if not ok:
        return "폴더 권한을 다 거두지 못해 연결을 끊지 않았습니다. 잠시 뒤 다시 시도해 주세요", False
    if email:
        _drop_if_orphan(request, email)
    return f"{dept} 부서 드라이브 연결을 끊었습니다 · 폴더와 문서는 {email or '그 계정'} 드라이브에 남아 있습니다", True


@router.post("/team/drive/sync")
def team_drive_sync(request: Request, dept: str = Form(""), back: str = Form("")):
    dept = " ".join(dept.split())
    if not _known(request, dept) or not may_manage(request, dept):
        raise HTTPException(403, "부서 드라이브는 그 부서의 총괄 책임자와 관리자가 다룹니다")
    msg, ok = sync_now(request, dept)
    return _done(request, _back_of(back, dept), msg, ok, dept)


@router.post("/team/drive/disconnect")
def team_drive_disconnect(request: Request, dept: str = Form(""), back: str = Form("")):
    dept = " ".join(dept.split())
    if not _known(request, dept) or not may_manage(request, dept):
        raise HTTPException(403, "부서 드라이브는 그 부서의 총괄 책임자와 관리자가 다룹니다")
    msg, ok = disconnect_now(request, dept)
    return _done(request, _back_of(back, dept), msg, ok, dept)


@router.get("/team/drive/folders")
def team_drive_folders(request: Request, dept: str = "", parent: str = "root", q: str = "", token: str = ""):
    """위치 고르기 창 — 부서 계정의 내 드라이브·공유 드라이브 폴더(읽기 전용 탐색, folder_picker). 연결한 뒤에만."""
    import httpx
    from zzaimy.ingest.folder_picker import browse
    dept = " ".join(dept.split())
    if not _known(request, dept) or not may_manage(request, dept):
        raise HTTPException(403, "부서 드라이브는 그 부서의 총괄 책임자와 관리자가 다룹니다")
    st = request.app.state
    by = (st.db.get_setting(f"google_root_by:{dept}", "") or "").strip()
    if not by:
        return JSONResponse({"detail": "먼저 구글 드라이브를 연결해 주세요"}, status_code=409)
    try:
        got = browse(by, parent or "root", q, token)
    except (ValueError, httpx.HTTPError):
        return JSONResponse({"detail": "폴더를 불러오지 못했습니다. 잠시 뒤 다시 시도해 주세요"}, status_code=400)
    here = (st.db.get_setting(f"google_root:{dept}", "") or "").strip()
    got["items"] = [x for x in got.get("items", []) if x.get("id") != here]      # 부서 폴더 자신은 고를 수 없다
    return JSONResponse({**got, "account": by, "folder_name": dept_drive.folder_name(dept)})


@router.post("/team/drive/move")
def team_drive_move(request: Request, dept: str = Form(""), parent: str = Form("root"), back: str = Form("")):
    """부서 폴더 위치 바꾸기 — 고른 폴더 아래로 옮긴다(안의 문서도 함께). 쓸 수 있는 폴더인지 부서 계정으로 다시 본다."""
    import httpx
    from zzaimy.ingest.folder_picker import folder as check_folder
    dept = " ".join(dept.split())
    if not _known(request, dept) or not may_manage(request, dept):
        raise HTTPException(403, "부서 드라이브는 그 부서의 총괄 책임자와 관리자가 다룹니다")
    st = request.app.state
    by = (st.db.get_setting(f"google_root_by:{dept}", "") or "").strip()
    back = _back_of(back, dept)
    parent = (parent or "root").strip()
    if not by:
        return _done(request, back, "먼저 구글 드라이브를 연결해 주세요", False, dept)
    if parent != "root":
        try:
            if not check_folder(by, parent)["writable"]:
                return _done(request, back, "그 폴더에는 부서 계정이 쓸 권한이 없습니다", False, dept)
        except (ValueError, httpx.HTTPError):
            return _done(request, back, "고른 위치를 확인하지 못했습니다", False, dept)
    try:
        dept_drive.move(st.db, dept, parent)
    except Exception as e:
        return _done(request, back, str(e)[:200], False, dept)
    return _done(request, back, f"{dept_drive.folder_name(dept)} 폴더를 옮겼습니다", True, dept)
