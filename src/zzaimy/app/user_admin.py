"""사용자 관리와 사용자별 구글 계정 연결.

구글 API(앱 등록, OAuth 클라이언트)는 개발자 계정이 한 번 해 두고, 관리자가 플랫폼 계정을 만들면 사용자마다 자기 학교 계정
(ync.ac.kr)을 연결한다. 구글 허용은 그 계정 주인이 로그인해야 하므로 본인만 할 수 있다(설정 화면의 '내 구글 계정'). 관리자는
/dev/users 에서 계정을 만들고(역할·부서·사용 여부·비밀번호 초기화), 누가 어느 구글 계정에 이어졌는지 보고, 이음을 풀거나 이미
연결된 계정으로 다시 지정하고, 허용 도메인·부서 공용 계정·부서 공유 드라이브를 정한다. 문서 작성은 각자 연결한 학교 계정으로
한다.

- 사용자: GET /account/google(상태 JSON), GET /account/google/connect(허용 화면으로), POST /account/google/disconnect
- 관리자(/dev 는 개발자 역할만 — main.py 의 check_auth): GET /dev/users, POST /dev/users/save,
  POST /dev/google/{settings,dept,revoke}

main.py 는 app.state 에 accounts(플랫폼 계정 표)·save_accounts·set_pw·new_account·templates·page_ctx 를 두고 이 라우터를 붙인다.
"""

from __future__ import annotations

import os
import re
import httpx
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from zzaimy.app import passwords
from zzaimy.app.access_guard import ROLES
from zzaimy.ingest import gdocs, gdrive, gdrive_files

router = APIRouter()

_UID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")          # 점은 안 된다 — 세션 토큰이 '만료.아이디.서명' 꼴
ASSIGNABLE_ROLES = {key: ROLES[key] for key in ("staff", "head", "dev")}
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
        # 본인 연결도 부서 공용도 아니면 공용 계정 대신 쓰기(google_fallback_shared) — 표에서 본인 연결처럼 보이지 않게 이름을 붙인다
        via = "본인" if effective == bound else "부서 공용" if effective == dept_acct else "공용 계정 사용"
    return {"user": uid, "bound": bound, "bound_ok": bool(bound) and bound in conn, "effective": effective, "via": via,
            "domain": gdrive.allowed_domain(), "app_ready": gdrive.configured()}


def known_depts(db, accounts: dict, data_dir=None) -> list[str]:
    """알려진 부서 — 계정에 지정된 부서, 부서 공용 계정·저장 위치를 정한 부서, RAG 공간 설정(원본 폴더 담당 부서
    dept_of_area · 공간의 depts · 계정별 추가 권한 dept:<부서>). 사용자 편집 화면이 쓴다.
    rag_spaces.known_depts(폴더 짝·계정·공간) 위에 부서 공용 설정·추가 권한 부서를 더한다.
    「공통」은 문서 쪽 꼬리표(부서 없는 사업 문서)라 사용자 부서가 아니다 — 부서를 정하지 않으면 공통 RAG 만 찾는다."""
    from zzaimy.app import rag_spaces

    cfg = rag_spaces.config(data_dir)
    names = set(rag_spaces.known_depts(cfg, accounts or {}))          # 폴더 짝·계정·공간 설정 — /dev/rag 와 같은 목록
    names |= {k.split(":", 1)[1].strip() for k, v in (db.all_settings() if db is not None else {}).items()
              if k.startswith(("google_account_dept:", "google_root:")) and (v or "").strip()}
    for granted in (cfg.get("grants") or {}).values():
        names |= {str(sid)[5:].strip() for sid in granted or [] if str(sid).startswith("dept:")}
    return sorted(n for n in names if n and n != "공통")


def overview(db, accounts: dict, data_dir=None) -> dict:
    conn = connected_emails()
    users = []
    for uid, a in sorted(accounts.items(), key=lambda kv: (not kv[1].get("active", True), kv[1].get("dept") or "", kv[0])):
        st = user_status(db, uid, a.get("dept") or None)
        users.append({**st, "name": a.get("name", ""), "role": a.get("role", "staff"), "role_label": ROLES.get(a.get("role", "staff"), ""),
                      "dept": a.get("dept", ""), "active": a.get("active", True), "created_at": (a.get("created_at") or "")[:10]})
    settings = db.all_settings()
    # 부서 목록은 한 곳(known_depts) — 여기서 만든 부서가 RAG 공간의 부서 RAG·검색 범위·폴더 범위에 그대로 쓰인다
    depts = known_depts(db, accounts, data_dir)
    n_docs: dict[str, int] = {}
    if depts:
        with db._conn() as c:
            n_docs = {str(r[0]): int(r[1]) for r in c.execute(
                f"SELECT dept, COUNT(*) FROM documents WHERE dept IN ({','.join('?' * len(depts))}) GROUP BY dept", depts)}
    dept_rows = [{"dept": d, "account": (settings.get(f"google_account_dept:{d}") or "").strip(),
                  "root": (settings.get(f"google_root:{d}") or "").strip(),
                  "members": sorted(u for u, a in accounts.items() if (a.get("dept") or "").strip() == d),
                  "docs": n_docs.get(d, 0)} for d in depts]
    owners: dict[str, list[str]] = {}
    sharers: dict[str, list[str]] = {}         # 본인 연결 없이 이 계정으로 문서를 쓰는 사용자(부서 공용·공용 계정)
    for u in users:
        if u["bound"]:
            owners.setdefault(u["bound"], []).append(u["user"])
        if u["effective"] and u["via"] != "본인":
            sharers.setdefault(u["effective"], []).append(f'{u["user"]}({u["via"]})')
    emails = [{"email": e, "users": owners.get(e, []), "shared": sharers.get(e, []), "docs_ok": gdocs.has_docs_scope(e),
               "granted_at": conn[e].get("granted_at", "")} for e in sorted(conn)]
    return {"users": users, "depts": dept_rows, "emails": emails, "domain": gdrive.allowed_domain(),
            "app": gdrive.public_status(), "roles": ASSIGNABLE_ROLES,
            "me": ""}


# ---------------- 사용자 본인 ----------------

@router.get("/account/google")
def my_google(request: Request):
    """내 구글 연결 상태(JSON). 브라우저로 주소를 열면(text/html 요청) 설정 화면의 구글 계정 칸으로 보낸다."""
    if "text/html" in (request.headers.get("accept") or ""):
        return RedirectResponse("/settings#myGoogle", status_code=303)
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
    data = overview(st.db, _accounts(request), getattr(st, "data_dir", None))
    data["me"] = _user(request)
    data["dept_choices"] = known_depts(st.db, _accounts(request), getattr(st, "data_dir", None))
    return st.templates.TemplateResponse(request, "dev_users.html", st.page_ctx(request, {**data, "ok": ok, "err": err}))


@router.get("/dev/google")
def dev_google_moved():
    return RedirectResponse("/dev/users", status_code=301)


@router.post("/dev/users/save")
def dev_users_save(request: Request, uid: str = Form(""), is_new: str = Form(""), name: str = Form(""),
                   role: str = Form("staff"), dept: str = Form(""), active: str = Form(""), new_pw: str = Form(""),
                   google: str = Form("")):
    """사용자 추가·수정 — 역할·부서·사용 여부, 임시 비밀번호(새 계정은 필수, 기존 계정은 왔을 때만 초기화), 구글 계정 이음.
    관리자가 넣은 비밀번호는 임시다 — 계정에 must_change 를 달아 다음 로그인 때 본인이 바꾸게 한다. 값은 응답에 싣지 않는다."""
    st = request.app.state
    accounts = _accounts(request)
    uid = uid.strip().lower()
    new = bool(is_new)
    if role not in ROLES:
        return _back("역할 값이 올바르지 않습니다", ok=False)
    if role not in ASSIGNABLE_ROLES and (new or accounts.get(uid, {}).get("role") != role):
        return _back("새로 지정할 수 없는 역할입니다", ok=False)
    if new:
        if not _UID.match(uid):
            return _back("아이디는 영문 소문자·숫자로 3~32자입니다(_ - 가능)", ok=False)
        if uid in accounts:
            return _back(f"{uid} 는 이미 있는 아이디입니다", ok=False)
        if passwords.problem(new_pw):
            return _back(f"새 계정의 임시 비밀번호는 {passwords.MIN_LENGTH}자 이상이어야 합니다", ok=False)
    elif uid not in accounts:
        raise HTTPException(404, "계정이 없습니다")
    elif new_pw and passwords.problem(new_pw):
        return _back(f"임시 비밀번호는 {passwords.MIN_LENGTH}자 이상이어야 합니다", ok=False)
    on = bool(active) or new
    me = _user(request)
    if uid == me and (not on or role != "dev"):
        return _back("지금 로그인한 관리자 계정은 스스로 끄거나 역할을 낮출 수 없습니다", ok=False)
    others_dev = [u for u, a in accounts.items() if u != uid and a.get("role") == "dev" and a.get("active", True)]
    if not others_dev and (role != "dev" or not on):
        return _back("사용 중인 관리자 계정이 하나는 남아 있어야 합니다", ok=False)
    dept = dept.strip()
    if dept and dept not in known_depts(st.db, accounts, getattr(st, "data_dir", None)):
        return _back("부서는 목록에서 골라 주세요 — 새 부서는 「부서 공용」의 부서 추가로 먼저 만듭니다", ok=False)
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
    if new_pw:
        accounts[uid]["must_change"] = True
    accounts[uid].update(role=role, dept=dept, active=on, updated_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
    st.save_accounts()
    old = (st.db.get_setting(f"google_account:{uid}", "") or "").strip()
    if google != old:
        st.db.set_setting(f"google_account:{uid}", google)
    msg = f"{uid} 계정을 만들었습니다 — 임시 비밀번호를 전달하세요. 첫 로그인 때 새 비밀번호로 바꾸게 됩니다" if new else f"{uid} 계정을 저장했습니다"
    if new_pw and not new:
        msg += " — 임시 비밀번호로 바꿨습니다. 다음 로그인 때 새 비밀번호로 바꾸게 됩니다"
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
        from zzaimy.ingest.folder_picker import folder as check_folder
        email = (db.get_setting(f"google_account:{_user(request)}", "") or "").strip()
        if not email or email not in connected_emails():
            return _back("먼저 본인의 학교 계정을 연결해 주세요", ok=False)
        try:
            selected = check_folder(email, folder)
            if not selected['writable']:
                return _back("이 폴더에 문서를 저장할 권한이 없습니다", ok=False)
        except (ValueError, httpx.HTTPError):
            return _back("저장 위치를 확인하지 못했습니다. 계정 연결과 쓰기 권한을 확인해 주세요", ok=False)
        db.set_setting(f"google_root_name:{dept}", selected['name'])
    db.set_setting(f"google_account_dept:{dept}", account)
    db.set_setting(f"google_root:{dept}", folder)
    return _back(f"부서 {dept} 설정을 저장했습니다")


@router.post("/dev/users/dept")
def dev_users_dept(request: Request, action: str = Form(""), name: str = Form("")):
    """부서 만들기·지우기 — 부서 목록은 RAG 공간 설정(dept_rags)에 하나로 둔다. 만든 부서는 부서 RAG·검색 범위·폴더 범위·계정 부서에서
    바로 고를 수 있다. 지우면 그 부서 문서는 공통 RAG 로 돌아가고 추가 권한·폴더 짝·부서 공용 설정도 풀린다(계정이 있으면 못 지운다)."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    data_dir = getattr(st, "data_dir", None)
    cfg = dict(rag_spaces.config(data_dir))
    name = " ".join((name or "").split())
    if action == "create":
        new, err = rag_spaces.create_dept_rag(cfg, name, by=_user(request))
        if err:
            return _back(err, ok=False)
        rag_spaces.save_config(new, data_dir)
        return RedirectResponse(f"/dev/users?ok={quote(f'부서 「{name}」을 만들었습니다 — 계정 부서·부서 RAG·검색 범위에서 고를 수 있습니다')}#depts",
                                status_code=303)
    if action == "delete":
        if any((a.get("dept") or "").strip() == name for a in _accounts(request).values()):
            return _back(f"「{name}」 부서 계정이 있어 지울 수 없습니다 — 계정의 부서를 먼저 바꿉니다", ok=False)
        if name not in known_depts(st.db, _accounts(request), data_dir):
            return _back("없는 부서입니다", ok=False)
        new, moved = rag_spaces.delete_dept_rag(st.db, cfg, name)
        rag_spaces.save_config(new, data_dir)
        for k in (f"google_account_dept:{name}", f"google_root:{name}", f"google_root_name:{name}"):
            st.db.set_setting(k, "")
        return RedirectResponse(f"/dev/users?ok={quote(f'부서 「{name}」을 지웠습니다 · 문서 {moved}건은 공통 RAG 로')}#depts", status_code=303)
    return _back("알 수 없는 요청", ok=False)


@router.get("/dev/google/folders")
def dev_google_folders(request: Request, parent: str = 'root', q: str = '', token: str = ''):
    from zzaimy.ingest.folder_picker import browse
    import httpx
    email = (request.app.state.db.get_setting(f"google_account:{_user(request)}", "") or "").strip()
    if not email or email not in connected_emails():
        return JSONResponse({'error': '본인의 학교 계정을 먼저 연결해 주세요.', 'connect': '/account/google/connect'}, status_code=409)
    try:
        return JSONResponse(browse(email, parent, q, token))
    except (ValueError, httpx.HTTPError):
        return JSONResponse({'error': '목록을 불러오지 못했습니다. 계정 연결과 폴더 권한을 확인한 뒤 다시 시도해 주세요.'}, status_code=400)


def unlink_google(db, accounts: dict, email: str) -> None:
    """구글 계정 연결 해제 — 보관 토큰과 그 계정으로 이어진 사용자·부서 이음을 모두 푼다.
    사용자·계정 화면과 문서 반입(Google Drive) 화면이 같은 이 함수를 쓴다(토큰만 지우면 사용자 이음이 죽은 계정을 가리킨다)."""
    for uid, e in bindings(db, accounts).items():
        if e == email:
            db.set_setting(f"google_account:{uid}", "")
    for k, v in db.all_settings().items():
        if k.startswith("google_account_dept:") and (v or "").strip() == email:
            db.set_setting(k, "")
    gdrive.revoke(email)


@router.post("/dev/google/revoke")
def dev_google_revoke(request: Request, email: str = Form("")):
    """연결된 구글 계정을 지운다 — unlink_google."""
    email = email.strip()
    if not email:
        return _back("계정을 골라 주세요", ok=False)
    unlink_google(request.app.state.db, _accounts(request), email)
    return _back(f"{email} 연결을 지웠습니다(구글 쪽 권한은 그 계정의 보안 설정에서 따로 지울 수 있습니다)")
