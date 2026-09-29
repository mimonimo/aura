"""사용자 관리와 사용자별 구글 계정 연결(2026-09-30 사용자 확정).

구글 API(앱 등록, OAuth 클라이언트)는 개발자 계정이 한 번 해 두고, 관리자가 플랫폼 계정을 만들면 사용자마다 자기 학교 계정
(ync.ac.kr)을 연결한다. 구글 허용은 그 계정 주인이 로그인해야 하므로 본인만 할 수 있다(설정 화면의 '내 구글 계정'). 관리자는
/dev/users 에서 계정을 만들고(역할·부서·사용 여부·비밀번호 초기화), 누가 어느 구글 계정에 이어졌는지 보고, 이음을 풀거나 이미
연결된 계정으로 다시 지정하고, 허용 도메인·부서 공용 계정·부서 공유 드라이브를 정한다. 문서 작성은 각자 연결한 학교 계정으로
한다(사용자 확정 2026-09-30, 공용 계정 대체 없음).

- 사용자: GET /account/google(상태 JSON), GET /account/google/connect(허용 화면으로), POST /account/google/disconnect
- 관리자(/dev 는 개발자 역할만 — main.py 의 check_auth): GET /dev/users, POST /dev/users/save,
  POST /dev/google/{settings,dept,revoke}

main.py 는 app.state 에 accounts(플랫폼 계정 표)·save_accounts·set_pw·new_account·templates·page_ctx 를 두고 이 라우터를 붙인다.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from zzaimy.app.access_guard import ROLES
from zzaimy.ingest import gdocs, gdrive, gdrive_files

router = APIRouter()

_UID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")          # 점은 안 된다 — 세션 토큰이 '만료.아이디.서명' 꼴
_FOLDER_ID = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]{10,})|^([A-Za-z0-9_-]{10,})$")


def redirect_uri(request: Request) -> str:
    """구글에 등록한 되돌아올 주소 — main.py 의 _gdrive_redirect_uri 와 같은 경로(콘솔에 새 주소를 넣지 않아도 되게)."""
    base = os.environ.get("ZZAIMY_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/dev/gdrive/callback"


def _user(request: Request) -> str:
    return getattr(request.state, "user", "") or ""


def _accounts(request: Request) -> dict:
    return getattr(request.app.state, "accounts", {}) or {}


def connected_emails() -> dict[str, dict]:
    return {a["email"]: a for a in gdrive.list_accounts()}


def bindings(db, uids) -> dict[str, str]:
    return {uid: (db.get_setting(f"google_account:{uid}", "") or "").strip() for uid in uids}


def user_status(db, uid: str, dept: str | None) -> dict:
    """한 사용자의 연결 상태 — 이어진 계정, 그 허용이 살아 있는지, 문서를 실제로 만들 계정과 그 출처(본인·부서 공용·공용)."""
    conn = connected_emails()
    bound = (db.get_setting(f"google_account:{uid}", "") or "").strip()
    effective = gdrive_files.account_for(db, uid, dept or None)
    dept_acct = (db.get_setting(f"google_account_dept:{dept}", "") or "").strip() if dept else ""
    via = ""
    if effective:
        via = "본인" if effective == bound else "부서 공용" if effective == dept_acct else ""
    return {"user": uid, "bound": bound, "bound_ok": bool(bound) and bound in conn, "effective": effective, "via": via,
            "domain": gdrive.allowed_domain(), "app_ready": gdrive.configured()}


def overview(db, accounts: dict) -> dict:
    conn = connected_emails()
    users = []
    for uid, a in sorted(accounts.items(), key=lambda kv: (not kv[1].get("active", True), kv[1].get("dept") or "", kv[0])):
        st = user_status(db, uid, a.get("dept") or None)
        users.append({**st, "name": a.get("name", ""), "role": a.get("role", "staff"), "role_label": ROLES.get(a.get("role", "staff"), ""),
                      "dept": a.get("dept", ""), "active": a.get("active", True), "created_at": (a.get("created_at") or "")[:10]})
    settings = db.all_settings()
    depts = sorted({a.get("dept") for a in accounts.values() if a.get("dept")} |
                   {k.split(":", 1)[1] for k, v in settings.items() if k.startswith(("google_account_dept:", "google_root:")) and v})
    dept_rows = [{"dept": d, "account": (settings.get(f"google_account_dept:{d}") or "").strip(),
                  "root": (settings.get(f"google_root:{d}") or "").strip()} for d in depts]
    owners: dict[str, list[str]] = {}
    for u in users:
        if u["bound"]:
            owners.setdefault(u["bound"], []).append(u["user"])
    emails = [{"email": e, "users": owners.get(e, []), "docs_ok": gdocs.has_docs_scope(e),
               "granted_at": conn[e].get("granted_at", "")} for e in sorted(conn)]
    return {"users": users, "depts": dept_rows, "emails": emails, "domain": gdrive.allowed_domain(),
            "app": gdrive.public_status(), "roles": ROLES,
            "me": ""}


# ---------------- 사용자 본인 ----------------

@router.get("/account/google")
def my_google(request: Request):
    a = _accounts(request).get(_user(request), {})
    return JSONResponse(user_status(request.app.state.db, _user(request), a.get("dept") or None))


@router.get("/account/google/connect")
def my_google_connect(request: Request, back: str = "/settings"):
    try:
        url = gdrive.auth_url(redirect_uri(request))
    except ValueError as e:
        return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
    resp = RedirectResponse(url, status_code=303)
    # 허용 뒤 돌아갈 화면 — 개발자가 아닌 사용자는 /dev 화면을 볼 수 없다(main.py 콜백이 읽는다)
    safe = back if back.startswith("/") and not back.startswith("//") else "/settings"
    resp.set_cookie("zz_google_back", safe, max_age=900, httponly=True, samesite="lax")
    return resp


@router.post("/account/google/disconnect")
def my_google_disconnect(request: Request):
    db = request.app.state.db
    uid = _user(request)
    email = (db.get_setting(f"google_account:{uid}", "") or "").strip()
    db.set_setting(f"google_account:{uid}", "")
    _drop_token_if_orphan(db, request, email)
    return RedirectResponse(f"/settings?ok={quote('구글 계정 연결을 해제했습니다')}", status_code=303)


def _drop_token_if_orphan(db, request: Request, email: str) -> None:
    """아무 사용자·부서도 쓰지 않는 계정이면 보관한 토큰도 지운다."""
    if not email:
        return
    used = set(bindings(db, _accounts(request)).values())
    used |= {(v or "").strip() for k, v in db.all_settings().items() if k.startswith("google_account_dept:")}
    if email not in used:
        gdrive.revoke(email)


# ---------------- 관리자: 사용자 ----------------

def _back(msg: str, ok: bool = True) -> RedirectResponse:
    return RedirectResponse(f"/dev/users?{'ok' if ok else 'err'}={quote(msg)}", status_code=303)


@router.get("/dev/users", response_class=HTMLResponse)
def dev_users(request: Request, ok: str = "", err: str = ""):
    st = request.app.state
    data = overview(st.db, _accounts(request))
    data["me"] = _user(request)
    return st.templates.TemplateResponse(request, "dev_users.html", st.page_ctx(request, {**data, "ok": ok, "err": err}))


@router.get("/dev/google")
def dev_google_moved():
    return RedirectResponse("/dev/users", status_code=301)


@router.post("/dev/users/save")
def dev_users_save(request: Request, uid: str = Form(""), is_new: str = Form(""), name: str = Form(""),
                   role: str = Form("staff"), dept: str = Form(""), active: str = Form(""), new_pw: str = Form(""),
                   google: str = Form("")):
    """사용자 추가·수정 — 역할·부서·사용 여부, 비밀번호(새 계정은 필수, 기존 계정은 적으면 초기화), 구글 계정 이음."""
    st = request.app.state
    accounts = _accounts(request)
    uid = uid.strip().lower()
    new = bool(is_new)
    if role not in ROLES:
        return _back("역할 값이 올바르지 않습니다", ok=False)
    if new:
        if not _UID.match(uid):
            return _back("아이디는 영문 소문자·숫자로 3~32자입니다(_ - 가능)", ok=False)
        if uid in accounts:
            return _back(f"{uid} 는 이미 있는 아이디입니다", ok=False)
        if len(new_pw) < 8:
            return _back("새 계정의 초기 비밀번호는 8자 이상이어야 합니다", ok=False)
    elif uid not in accounts:
        raise HTTPException(404, "계정이 없습니다")
    elif new_pw and len(new_pw) < 8:
        return _back("비밀번호는 8자 이상이어야 합니다", ok=False)
    on = bool(active) or new
    me = _user(request)
    if uid == me and (not on or role != "dev"):
        return _back("지금 로그인한 관리자 계정은 스스로 끄거나 역할을 낮출 수 없습니다", ok=False)
    others_dev = [u for u, a in accounts.items() if u != uid and a.get("role") == "dev" and a.get("active", True)]
    if not others_dev and (role != "dev" or not on):
        return _back("사용 중인 관리자 계정이 하나는 남아 있어야 합니다", ok=False)
    google = google.strip()
    if google:
        if google not in connected_emails():
            return _back("연결(허용)된 구글 계정만 지정할 수 있습니다 — 그 사용자가 먼저 설정 화면에서 연결해야 합니다", ok=False)
        dom = gdrive.allowed_domain()
        if dom and not google.lower().endswith("@" + dom):
            return _back(f"{dom} 계정만 지정할 수 있습니다", ok=False)
    if new:
        accounts[uid] = st.new_account(uid, new_pw, role, name.strip())
    else:
        if new_pw:
            st.set_pw(accounts[uid], new_pw)
        accounts[uid]["name"] = name.strip()
    accounts[uid].update(role=role, dept=dept.strip(), active=on, updated_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
    st.save_accounts()
    old = (st.db.get_setting(f"google_account:{uid}", "") or "").strip()
    if google != old:
        st.db.set_setting(f"google_account:{uid}", google)
    msg = f"{uid} 계정을 만들었습니다 — 초기 비밀번호를 전달하고 첫 로그인 뒤 바꾸게 하세요" if new else f"{uid} 계정을 저장했습니다"
    if new_pw and not new:
        msg += "(비밀번호 초기화)"
    return _back(msg)


# ---------------- 관리자: 구글 연동 ----------------

@router.post("/dev/google/settings")
def dev_google_settings(request: Request, domain: str = Form("")):
    try:
        d = gdrive.set_domain(domain)
    except ValueError as e:
        return _back(str(e), ok=False)
    return _back(f"허용 도메인을 저장했습니다({d or '제한 없음'})")


@router.post("/dev/google/dept")
def dev_google_dept(request: Request, dept: str = Form(""), account: str = Form(""), root: str = Form("")):
    """부서 공용 계정(미연결 담당자가 쓰는 계정)과 부서 공유 드라이브 자리(폴더 주소나 id)."""
    db = request.app.state.db
    dept, account, root = dept.strip(), account.strip(), root.strip()
    if not dept:
        return _back("부서 이름을 적어 주세요", ok=False)
    if account and account not in connected_emails():
        return _back("연결(허용)된 구글 계정만 부서 공용으로 지정할 수 있습니다", ok=False)
    folder = ""
    if root:
        m = _FOLDER_ID.search(root)
        if not m:
            return _back("공유 드라이브는 폴더 주소나 폴더 id 로 적어 주세요", ok=False)
        folder = m.group(1) or m.group(2)
    db.set_setting(f"google_account_dept:{dept}", account)
    db.set_setting(f"google_root:{dept}", folder)
    return _back(f"부서 {dept} 설정을 저장했습니다")


@router.post("/dev/google/revoke")
def dev_google_revoke(request: Request, email: str = Form("")):
    """연결된 구글 계정을 지운다 — 보관 토큰과 그 계정으로 이어진 사용자·부서 이음을 모두 푼다."""
    db = request.app.state.db
    email = email.strip()
    if not email:
        return _back("계정을 골라 주세요", ok=False)
    for uid, e in bindings(db, _accounts(request)).items():
        if e == email:
            db.set_setting(f"google_account:{uid}", "")
    for k, v in db.all_settings().items():
        if k.startswith("google_account_dept:") and (v or "").strip() == email:
            db.set_setting(k, "")
    gdrive.revoke(email)
    return _back(f"{email} 연결을 지웠습니다(구글 쪽 권한은 그 계정의 보안 설정에서 따로 지울 수 있습니다)")
