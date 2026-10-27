"""사용자 관리와 사용자별 구글 계정 연결.

구글 API(앱 등록, OAuth 클라이언트)는 개발자 계정이 한 번 해 두고, 관리자가 플랫폼 계정을 만들면 사용자마다 자기 학교 계정
(ync.ac.kr)을 연결한다. 구글 허용은 그 계정 주인이 로그인해야 하므로 본인만 할 수 있다(설정 화면의 「구글 계정」). 관리자는
조직 관리(/team)에서 계정을 만들고(역할·부서·사용 여부·비밀번호 초기화), 누가 어느 구글 계정에 이어졌는지 보고, 이음을 풀거나 이미
연결된 계정으로 다시 지정하고, 허용 도메인·부서 공용 계정·부서 공유 드라이브를 정한다. 문서 작성은 각자 연결한 학교 계정으로
한다.

- 사용자: GET /account/google(상태 JSON), GET /account/google/connect(허용 화면으로), POST /account/google/disconnect
- 관리자(개발자 역할만 — 처리마다 need_admin 으로 막는다): POST /team/accounts/save(역할·교직원 등급·부서 —
  등급과 관리자 상한은 grades, ADR-0066),
  POST /team/google/revoke(연결 지우기), POST /team/accounts/bulk(여러 명).
  화면은 조직 관리(/team) 한 곳이다 — 계정·부서·구성원, 관리자에게는 구글 앱 등록과 학교 계정 연결 현황(_team_google.html)까지
  그 화면에서 다룬다. 고치기 경로도 화면을 따라 /team/accounts/*·/team/google/* 이다(옛 /dev/users/*·/dev/google/* 는 10-27 에 지움) —
  /dev 앞부분 막기(main.check_auth)를 받지 않으니 처리마다 need_admin 을 먼저 부른다. 부서 만들기·지우기·
  상위 부서는 조직 관리의 트리(org_routes /org/*)에서 한다. 계정의 겸직 부서는
  줄마다 부서 + 그 부서에서의 등급(account["dept_grades"] 한 곳 — 일반 겸직도 member 로 적는다)
- 조직 관리: GET /team(부서 트리 + 상세), GET /api/org/accounts(전체 사용자)·/api/org/members(구성원), POST /api/org/members
  (옮기기·등급·추가 권한·빼기·사람 추가). 트리 자료와 부서 고치기는 org_routes(이 라우터에 함께 붙는다)

main.py 는 app.state 에 accounts(플랫폼 계정 표)·save_accounts·set_pw·new_account·templates·page_ctx 를 두고 이 라우터를 붙인다.
"""

from __future__ import annotations

import os
import re
import httpx
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from zzaimy.app import grades, org_lock, passwords
from zzaimy.app.access_guard import ROLES
from zzaimy.ingest import gdocs, gdrive, gdrive_files

router = APIRouter()

_UID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")          # 점은 안 된다 — 세션 토큰이 '만료.아이디.서명' 꼴
ASSIGNABLE_ROLES = {key: ROLES[key] for key in ("staff", "dev")}
_FOLDER_ID = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]{10,})|^([A-Za-z0-9_-]{10,})$")


def redirect_uri(request: Request) -> str:
    """구글에 등록한 되돌아올 주소 — main.py 의 _gdrive_redirect_uri 와 같은 경로(콘솔에 새 주소를 넣지 않아도 되게)."""
    base = os.environ.get("ZZAIMY_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/dev/gdrive/callback"


def need_admin(request: Request) -> None:
    """관리자(개발자 역할)만 — /dev 밖으로 옮긴 관리자 처리(/team/accounts/*·/team/google/*)가 처음에 부른다.
    역할은 check_auth 가 정한 request.state.role(테스트 보기 중이면 고른 역할)이라 /dev 앞부분 막기와 같은 판정이다."""
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "관리자 계정만 하는 일입니다")


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
    effective = gdrive_files.doc_account_for(db, uid, dept or None)
    dept_acct = (db.get_setting(f"google_account_dept:{dept}", "") or "").strip() if dept else ""
    via = ""
    if effective:
        # 본인 연결도 부서 공용도 아니면 공용 계정 대신 쓰기(google_fallback_shared) — 표에서 본인 연결처럼 보이지 않게 이름을 붙인다
        via = "본인" if effective == bound else "부서 공용" if effective == dept_acct else "공용 계정 사용"
    return {"user": uid, "bound": bound, "bound_ok": bool(bound) and bound in conn, "effective": effective, "via": via,
            "domain": gdrive.allowed_domain(), "app_ready": gdrive.configured()}


def known_depts(db, accounts: dict, data_dir=None) -> list[str]:
    """알려진 부서 — 계정에 지정된 부서, 부서 공용 계정·저장 위치를 정한 부서, RAG 공간 설정(원본 폴더 담당 부서
    dept_of_area · 공간의 depts). 사용자 편집 화면이 쓴다.
    rag_spaces.known_depts(폴더 짝·계정·공간) 위에 부서 공용 설정 부서를 더한다.
    「공통」은 문서 쪽 꼬리표(부서 없는 사업 문서)라 사용자 부서가 아니다 — 부서를 정하지 않으면 공통 RAG 만 찾는다."""
    from zzaimy.app import rag_spaces

    cfg = rag_spaces.config(data_dir)
    names = set(rag_spaces.known_depts(cfg, accounts or {}))          # 폴더 짝·계정·공간 설정 — /dev/rag 와 같은 목록
    names |= {k.split(":", 1)[1].strip() for k, v in (db.all_settings() if db is not None else {}).items()
              if k.startswith(("google_account_dept:", "google_root:")) and (v or "").strip()}
    return sorted(n for n in names if n and n != "공통")


def _extras(uid: str, a: dict, data_dir=None) -> list[str]:
    """겸직 부서 — 부서별 등급(dept_grades, 일반 겸직 포함)에 적힌 부서(소속 부서 빼고)."""
    from zzaimy.app import access_policy
    return access_policy.extra_depts(uid, data_dir, account=a)


class _SettingsSnap:
    """설정값을 한 번 읽어 둔 것 — 계정마다 설정을 여러 번 묻는 user_status 를 계정 수만큼 돌릴 때(읽기 전용)."""

    def __init__(self, db):
        self._d = dict(db.all_settings() or {}) if db is not None else {}

    def get_setting(self, key: str, default=None):
        return self._d.get(key, default)


def overview(db, accounts: dict, data_dir=None) -> dict:
    conn = connected_emails()
    users = []
    snap = _SettingsSnap(db)
    for uid, a in sorted(accounts.items(), key=lambda kv: (not kv[1].get("active", True), kv[1].get("dept") or "", kv[0])):
        st = user_status(snap, uid, a.get("dept") or None)
        g = grades.grade_of(a)
        users.append({**st, "name": a.get("name", ""), "role": a.get("role", "staff"), "role_label": ROLES.get(a.get("role", "staff"), ""),
                      "grade": g if g in grades.GRADES else "", "grade_label": grades.GRADES.get(g, ""),
                      "perms": [k for k in grades.PERMS if k in (a.get("perms") or ())] if a.get("role") in grades.STAFF else [],
                      "dept": a.get("dept", ""), "extra": (ex := _extras(uid, a, data_dir)),
                      "extra_grades": [grades.dept_grades(a).get(d, "member") for d in ex],
                      "active": a.get("active", True), "created_at": (a.get("created_at") or "")[:10]})
    owners: dict[str, list[str]] = {}
    sharers: dict[str, list[str]] = {}         # 본인 연결 없이 이 계정으로 문서를 쓰는 사용자(부서 공용·공용 계정)
    for u in users:
        if u["bound"]:
            owners.setdefault(u["bound"], []).append(u["user"])
        if u["effective"] and u["via"] != "본인":
            sharers.setdefault(u["effective"], []).append(f'{u["user"]}({u["via"]})')
    emails = [{"email": e, "users": owners.get(e, []), "shared": sharers.get(e, []), "docs_ok": gdocs.has_docs_scope(e),
               "granted_at": conn[e].get("granted_at", "")} for e in sorted(conn)]
    return {"users": users, "emails": emails, "domain": gdrive.allowed_domain(),
            "app": gdrive.public_status(), "roles": ASSIGNABLE_ROLES, "grades": grades.GRADES, "perms": grades.PERMS,
            "default_grade": grades.DEFAULT_GRADE, "admin_max": grades.ADMIN_MAX,
            "admin_n": grades.admin_count(accounts), "me": ""}


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
        url = gdrive.auth_url(redirect_uri(request), _user(request))
    except ValueError as e:
        return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
    resp = RedirectResponse(url, status_code=303)
    # 허용 뒤 돌아갈 화면 — 개발자가 아닌 사용자는 /dev 화면을 볼 수 없다(main.py 콜백이 읽는다)
    safe = back if re.fullmatch(r"/(?![/\\])[^\s\\]*", back or "") else "/settings"     # 서비스 안 경로만(「/\\…」·공백 금지)
    resp.set_cookie("zz_google_back", safe, max_age=900, httponly=True, samesite="lax")
    return resp


@router.post("/account/google/disconnect")
def my_google_disconnect(request: Request):
    db = request.app.state.db
    uid = _user(request)
    email = (db.get_setting(f"google_account:{uid}", "") or "").strip()
    hook = getattr(request.app.state, "before_google_disconnect", None)
    revoked = True
    if hook and email:
        try:
            revoked = hook(uid, email) is not False       # 이 계정이 준 작업 문서 보기 권한을 먼저 거둔다
        except Exception:
            revoked = False
    if not revoked:
        # 거두지 못한 공유가 남은 채 토큰을 지우면 그 공유를 다시는 거둘 수 없고, 몰래 토큰을 남기면 끊겠다는 뜻을 어긴다 —
        # 끊지 않고 그대로 알린다(다시 시도하거나 관리자가 드라이브에서 정리)
        return RedirectResponse("/settings?err=" + quote("작업 문서 공유 권한을 거두지 못해 연결을 끊지 않았습니다 — 잠시 뒤 다시 시도해 주세요. "
                                                          "계속되면 관리자에게 알려 주세요.") + "#myGoogle", status_code=303)
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

def _back(msg: str, ok: bool = True, to: str = "/team#google-links") -> RedirectResponse:
    """관리자 동작 뒤 돌아갈 곳 — 기본은 조직 관리의 학교 계정 연결 현황(구글 앱·허용 도메인·연결 삭제). 조각(#…)은 물음표 뒤로 보낸다."""
    path, _, frag = to.partition("#")
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{'ok' if ok else 'err'}={quote(msg)}" + (f"#{frag}" if frag else ""), status_code=303)


def dict_candidates(st, existing: list[str]) -> dict:
    """부서 사전(dept_dictionary)에서 고를 후보 — 만들 수 있는 것(행정·사업 조직, 이미 있는 부서 제외)과 건너뛴 까닭.
    화면은 이것을 「사전에서 고르기」 목록·부서 이름 자동완성·계정 부서 목록의 보조 후보로 쓴다. 자동으로 만들지는 않는다."""
    from zzaimy.app import dept_dictionary
    d = dept_dictionary.current(getattr(st, "db", None), getattr(st, "data_dir", None))
    plan = dept_dictionary.plan_create(d, existing)
    labels = {x: d.line(x) for x in existing if d.normalize(x)}
    return {"dict_ready": bool(d.units), "dict_create": [d.view(d.by_name[n]) for n in plan["create"]],
            "dict_skipped": len(plan["skip"]), "dept_labels": labels}


@router.post("/team/accounts/save")
@org_lock.serialized                                   # 조직·계정 저장값 공용 잠금(org_lock)
def team_accounts_save(request: Request, uid: str = Form(""), is_new: str = Form(""), name: str = Form(""),
                   role: str = Form("staff"), dept: str = Form(""), active: str = Form(""), new_pw: str = Form(""),
                   google: str = Form(""), extra: list[str] = Form([]), grade: str = Form(""),
                   perm: list[str] = Form([]), extra_grade: list[str] = Form([])):
    """사용자 추가·수정 — 역할·부서·사용 여부, 임시 비밀번호(새 계정은 필수, 기존 계정은 왔을 때만 초기화), 구글 계정 이음.
    관리자가 넣은 비밀번호는 임시다 — 계정에 must_change 를 달아 다음 로그인 때 본인이 바꾸게 한다. 값은 응답에 싣지 않는다."""
    need_admin(request)
    st = request.app.state
    accounts = _accounts(request)
    uid = uid.strip().lower()
    new = bool(is_new)
    if not new and uid not in accounts and role in ASSIGNABLE_ROLES:
        raise HTTPException(404, "계정이 없습니다")
    err = _save_one(st, accounts, _user(request), uid=uid, new=new, name=name.strip(), role=role, dept=dept.strip(),
                    on=bool(active) or new, new_pw=new_pw, google=google.strip(), extra=extra, extra_grade=extra_grade,
                    grade=grade.strip(), perm=perm)
    if err:
        return _back(err, ok=False, to="/team?view=all")
    _sync_drives_later(st, accounts)
    msg = f"{uid} 계정을 만들었습니다 — 임시 비밀번호를 전달하세요. 첫 로그인 때 새 비밀번호로 바꾸게 됩니다" if new else f"{uid} 계정을 저장했습니다"
    if new_pw and not new:
        msg += " — 임시 비밀번호로 바꿨습니다. 다음 로그인 때 새 비밀번호로 바꾸게 됩니다"
    return _back(msg, to="/team?view=all")


def _sync_drives_later(st, accounts: dict) -> None:
    """부서 드라이브 권한 — 사람이 들고 난 부서들을 뒤에서 맞춘다(소속·추가 부서가 바뀌었거나 계정을 껐을 때)."""
    import threading
    from zzaimy.ingest import dept_drive
    data_dir = getattr(st, "data_dir", None)
    touched = {d for d in known_depts(st.db, accounts, data_dir)
               if (st.db.get_setting(f"google_root_by:{d}", "") or "").strip()}
    if touched:
        threading.Thread(target=lambda: [dept_drive.sync(st.db, d, accounts, data_dir) for d in sorted(touched)],
                         name="dept-drive-sync", daemon=True).start()


def _save_one(st, accounts: dict, me: str, *, uid: str, new: bool, name: str, role: str, dept: str, on: bool,
              new_pw: str = "", google: str | None = None, extra: list[str] = (), extra_grade: list[str] = (),
              grade: str = "", perm: list[str] = ()) -> str:
    """계정 하나를 검사하고 저장한다 — 문제면 사람이 읽을 문장, 저장했으면 빈 값. 편집 창(team_accounts_save)과 여러 명 바꾸기(bulk)가
    같은 이 함수를 쓴다. google 이 None 이면 구글 이음은 그대로 둔다."""
    if role not in ROLES:
        return "역할 값이 올바르지 않습니다"
    if role not in ASSIGNABLE_ROLES and (new or accounts.get(uid, {}).get("role") != role):
        return "새로 지정할 수 없는 역할입니다"
    if new:
        if not _UID.match(uid):
            return "아이디는 영문 소문자·숫자로 3~32자입니다(_ - 가능)"
        if uid in accounts:
            return f"{uid} 는 이미 있는 아이디입니다"
        if passwords.problem(new_pw):
            return f"새 계정의 임시 비밀번호는 {passwords.MIN_LENGTH}자 이상이어야 합니다"
    elif uid not in accounts:
        return f"{uid} 계정이 없습니다"
    elif new_pw and passwords.problem(new_pw):
        return f"임시 비밀번호는 {passwords.MIN_LENGTH}자 이상이어야 합니다"
    if uid == me and (not on or role != "dev"):
        return "지금 로그인한 관리자 계정은 스스로 끄거나 역할을 낮출 수 없습니다"
    # 관리자는 개발자와 시스템 전체 관리자 둘까지(ADR-0066) — 이미 사용 중인 관리자 계정을 그대로 고쳐 저장하는 것은 막지 않는다
    was_admin = not new and accounts[uid].get("role") == "dev" and accounts[uid].get("active", True)
    if not was_admin and grades.admin_over(accounts, uid, role, on):
        return f"관리자는 최대 {grades.ADMIN_MAX}명까지 둘 수 있습니다 — 다른 관리자 계정을 교직원으로 바꾸거나 사용 중지한 뒤 지정해 주세요"
    if grade and grade not in grades.GRADES:
        return "등급 값이 올바르지 않습니다"
    others_dev = [u for u, a in accounts.items() if u != uid and a.get("role") == "dev" and a.get("active", True)]
    if not others_dev and (role != "dev" or not on):
        return "사용 중인 관리자 계정이 하나는 남아 있어야 합니다"
    data_dir = getattr(st, "data_dir", None)
    known = set(known_depts(st.db, accounts, data_dir))
    if dept and dept not in known:
        return "부서는 목록에서 골라 주세요 — 새 부서는 조직 관리에서 먼저 만듭니다"
    if google:
        if google not in connected_emails():
            return "연결(허용)된 구글 계정만 지정할 수 있습니다 — 그 사용자가 먼저 설정 화면에서 연결해야 합니다"
        dom = gdrive.allowed_domain()
        if dom and not google.lower().endswith("@" + dom):
            return f"{dom} 계정만 지정할 수 있습니다"
    if new:
        accounts[uid] = st.new_account(uid, new_pw, role, name)
    else:
        if new_pw:
            st.set_pw(accounts[uid], new_pw)
        accounts[uid]["name"] = name
    if new_pw:
        accounts[uid]["must_change"] = True
    accounts[uid].update(role=role, dept=dept, active=on, updated_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
    # 교직원 등급 — 교직원만 받는다. 고르지 않으면 지금 값 유지(새 계정은 기본 등급 사업 책임자), 교직원이 아니면 지운다
    if role in grades.STAFF:
        accounts[uid]["grade"] = grade or accounts[uid].get("grade") or grades.DEFAULT_GRADE
        if accounts[uid]["grade"] not in grades.GRADES:
            accounts[uid]["grade"] = grades.DEFAULT_GRADE
        # 등급과 따로 더 주는 권한(예: 일반 직원에게 프로젝트 만들기) — 아는 값만, 등급이 이미 주는 권한도 그대로 적어 둔다
        accounts[uid]["perms"] = [k for k in grades.PERMS if k in set(perm)]
        if not accounts[uid]["perms"]:
            accounts[uid].pop("perms", None)
    else:
        accounts[uid].pop("grade", None)
        accounts[uid].pop("perms", None)
    # 겸직 부서(여러 줄: 부서 + 그 부서에서의 등급) — 부서별 등급(dept_grades) 한 곳에 적는다(일반 겸직은 member).
    # 교직원만 받는다(학생·관리자는 비운다). 소속 부서·모르는 부서는 뺀다
    pairs: dict[str, str] = {}
    if role == "staff":
        for i, d in enumerate(extra):
            d = d.strip()
            g = (extra_grade[i] if i < len(extra_grade) else "").strip() or "member"
            if d and d in known and d != dept and g in grades.GRADES:
                pairs[d] = g
    dg = dict(pairs)
    if dg:
        accounts[uid]["dept_grades"] = dg
    else:
        accounts[uid].pop("dept_grades", None)
    st.save_accounts()
    if google is not None:
        old = (st.db.get_setting(f"google_account:{uid}", "") or "").strip()
        if google != old:
            st.db.set_setting(f"google_account:{uid}", google)
    return ""


BULK_OPS = {"dept": "부서를 옮겼습니다", "role": "역할을 바꿨습니다", "suspend": "사용 중지했습니다", "activate": "다시 사용하게 했습니다"}


@router.post("/team/accounts/bulk")
@org_lock.serialized                                   # 조직·계정 저장값 공용 잠금(org_lock)
def team_accounts_bulk(request: Request, op: str = Form(""), value: str = Form(""), uids: list[str] = Form([])):
    """고른 사용자 여러 명을 한 번에 — 부서 옮기기(value=부서, 빈 값은 부서 없음)·역할 바꾸기(value=역할)·사용 중지·다시 사용.
    사람마다 편집 창과 같은 검사(_save_one)를 거치고, 걸린 사람은 건너뛰어 까닭을 함께 돌려준다. 응답은 JSON {ok, msg, done, failed}."""
    need_admin(request)
    st = request.app.state
    accounts = _accounts(request)
    me = _user(request)
    if op not in BULK_OPS:
        return JSONResponse({"ok": False, "msg": "할 일을 골라 주세요"}, status_code=400)
    value = " ".join((value or "").split())
    if op == "role" and value not in ASSIGNABLE_ROLES:
        return JSONResponse({"ok": False, "msg": "바꿀 역할을 골라 주세요"}, status_code=400)
    data_dir = getattr(st, "data_dir", None)
    if op == "dept" and value and value not in known_depts(st.db, accounts, data_dir):
        return JSONResponse({"ok": False, "msg": "부서는 목록에서 골라 주세요"}, status_code=400)
    want = list(dict.fromkeys(u.strip().lower() for u in uids if u.strip()))
    if not want:
        return JSONResponse({"ok": False, "msg": "사용자를 골라 주세요"}, status_code=400)
    done, failed = [], []
    for uid in want:
        a = accounts.get(uid)
        if not a:
            failed.append({"uid": uid, "msg": "없는 계정입니다"})
            continue
        extras = _extras(uid, a, data_dir)
        eg = grades.dept_grades(a)
        role, dept, on = a.get("role", "staff"), str(a.get("dept") or "").strip(), a.get("active", True)
        if op == "dept":
            dept = value
        elif op == "role":
            role = value
        else:
            on = op == "activate"
        err = _save_one(st, accounts, me, uid=uid, new=False, name=a.get("name", ""), role=role, dept=dept, on=on,
                        google=None, extra=extras, extra_grade=[eg.get(d, "member") for d in extras],
                        grade=grades.grade_of(a) if grades.grade_of(a) in grades.GRADES else "",
                        perm=list(a.get("perms") or ()))
        (failed.append({"uid": uid, "msg": err}) if err else done.append(uid))
    if done:
        _sync_drives_later(st, accounts)
    label = BULK_OPS[op]
    msg = f"{len(done)}명 {label}" + (f" · {len(failed)}명은 바꾸지 못했습니다" if failed else "")
    if failed and not done:
        msg = failed[0]["msg"] if len(failed) == 1 else f"{len(failed)}명 모두 바꾸지 못했습니다 — {failed[0]['msg']}"
    return JSONResponse({"ok": bool(done), "msg": msg, "done": done, "failed": failed}, status_code=200 if done else 400)


from zzaimy.app.dept_drive_routes import router as _dept_drive_router  # noqa: E402  부서 쪽 연결(/team/drive*)

router.include_router(_dept_drive_router)


def dept_family(data_dir, name: str, known=None) -> list[str]:
    """그 부서 아래의 모든 부서(확정 위계 dept_parents 를 따라) — 깊은 것부터. known 을 주면 그 안의 이름만."""
    from zzaimy.app import dept_dictionary
    kids = dept_dictionary.children_map(data_dir)
    out, seen, todo = [], {name}, list(kids.get(name, []))
    while todo:
        c = todo.pop(0)
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
        todo += kids.get(c, [])
    if known is not None:
        out = [c for c in out if c in set(known)]
    return out[::-1]


KID_MODES = {"lift": "위로 올리기", "with": "함께 지우기"}


def delete_plan(st, accounts: dict, name: str, kids: str = "lift") -> dict:
    """지우기 전에 볼 것 — {"targets": 지울 부서(깊은 것부터, 마지막이 name), "lift": 위로 올릴 바로 아래 부서, "up": 올릴 자리,
    "blocked": 소속 사람이 있어 지울 수 없는 부서}. kids 는 lift(아래 부서를 지운 부서의 자리로 올린다)·with(아래 부서도 함께 지운다)."""
    from zzaimy.app import dept_dictionary
    data_dir = getattr(st, "data_dir", None)
    known = known_depts(st.db, accounts, data_dir)
    family = dept_family(data_dir, name, known)
    over = dept_dictionary.parents_override(data_dir)
    direct = sorted(c for c in family if over.get(c, "") == name)
    targets = (family if kids == "with" else []) + [name]
    has_people = {(a.get("dept") or "").strip() for a in accounts.values()}
    return {"targets": targets, "lift": [] if kids == "with" else direct, "up": over.get(name, ""),
            "blocked": [d for d in targets if d in has_people]}


@org_lock.serialized
def delete_dept(st, accounts: dict, name: str, kids: str = "lift") -> tuple[str, int]:
    """부서를 지운다 — (오류 문장, 공통 RAG 로 돌린 문서 수). 소속 계정이 있으면(함께 지울 아래 부서 포함) 하나도 지우지 않는다.
    아래 부서는 kids 대로 — lift: 지운 부서의 자리(그 상위, 없으면 맨 위 처·단)로 올리고, with: 함께 지운다.
    부서 RAG 문서는 공통으로, 부서 열어 주기(주는 쪽·받는 쪽)·폴더 짝·부서 공용 설정을 풀고, 겸직 등급·확정 위계에서도 뺀다.
    조직 관리(org_routes /org/delete)가 이 함수를 쓴다."""
    from zzaimy.app import dept_dictionary
    data_dir = getattr(st, "data_dir", None)
    name = " ".join((name or "").split())
    if kids not in KID_MODES:
        kids = "lift"
    if name not in known_depts(st.db, accounts, data_dir):
        return "없는 부서입니다", 0
    plan = delete_plan(st, accounts, name, kids)
    if plan["blocked"]:
        who = ", ".join(f"「{d}」" for d in plan["blocked"][:3]) + (" 등" if len(plan["blocked"]) > 3 else "")
        return f"{who} 부서에 사람이 있어 지울 수 없습니다 — 먼저 사람을 다른 부서로 옮기세요", 0
    if data_dir is not None:
        for c in plan["lift"]:                            # 지우기 전에 올린다(forget_dept 가 이 부서를 가리키던 관계를 지우므로)
            err = dept_dictionary.set_parent(data_dir, c, plan["up"])
            if err:
                return err, 0
    moved = 0
    for d in plan["targets"]:
        moved += _delete_one(st, accounts, d)
    return "", moved


def _delete_one(st, accounts: dict, name: str) -> int:
    """부서 하나의 흔적을 지운다(검사는 delete_dept 가 마쳤다) — 공통 RAG 로 돌린 문서 수."""
    from zzaimy.app import dept_dictionary, rag_spaces
    data_dir = getattr(st, "data_dir", None)
    new, moved = rag_spaces.delete_dept_rag(st.db, rag_spaces.config(data_dir), name)
    rag_spaces.save_config(new, data_dir)
    for k in (f"google_account_dept:{name}", f"google_root:{name}", f"google_root_name:{name}"):
        st.db.delete_setting(k)                          # 빈 값으로 남기지 않고 지운다
    # 보안: 지운 부서에 걸린 권한 흔적도 지운다 — 같은 이름으로 다시 만들었을 때 옛 겸직 등급·위계가 되살아나지 않게
    touched = False
    for a_ in accounts.values():
        dg = a_.get("dept_grades") or {}
        if name in dg:
            dg.pop(name, None)
            if not dg:
                a_.pop("dept_grades", None)
            touched = True
    if touched:
        org_lock.save_accounts(st)
    try:
        dept_dictionary.forget_dept(data_dir, name)
    except Exception:
        pass
    try:                                                 # 남은 흔적(RAG 최소 등급·사업 관리 dept: 지정·부서 공용 설정·공유 등) — 이 부서만이 아니라 없는 부서 이름 전부
        from zzaimy.app.org_routes import purge_traces, reconcile
        purge_traces(st, name)
        reconcile(st, explicit=True)
    except Exception:
        import logging
        logging.getLogger("zzaimy.app.user_admin").exception("부서 흔적 맞추기 실패")
    return moved


def delete_message(name: str, plan: dict, moved: int) -> str:
    """지운 결과 한 문장 — 함께 지운 아래 부서·올린 아래 부서·공통으로 돌린 문서 수."""
    msg = f"「{name}」 부서를 지웠습니다"
    if len(plan["targets"]) > 1:
        msg += f" · 아래 부서 {len(plan['targets']) - 1}개도 함께"
    if plan["lift"]:
        msg += f" · 아래 부서 {len(plan['lift'])}개는 " + (f"「{plan['up']}」 아래로" if plan["up"] else "처·단으로")
    return msg + f" · 문서 {moved}건은 공통 RAG 로"


def unlink_google(db, accounts: dict, email: str) -> None:
    """구글 계정 연결 해제 — 보관 토큰과 그 계정으로 이어진 사용자·부서 이음을 모두 푼다.
    조직 관리 「학교 계정 연결 현황」 의 연결 삭제(/team/google/revoke)가 쓴다(토큰만 지우면 사용자 이음이 죽은 계정을 가리킨다)."""
    for uid, e in bindings(db, accounts).items():
        if e == email:
            db.set_setting(f"google_account:{uid}", "")
    for k, v in db.all_settings().items():
        if k.startswith("google_account_dept:") and (v or "").strip() == email:
            db.set_setting(k, "")
    gdrive.revoke(email)


@router.post("/team/google/revoke")
def team_google_revoke(request: Request, email: str = Form("")):
    """연결된 구글 계정을 지운다 — unlink_google."""
    need_admin(request)
    email = email.strip()
    if not email:
        return _back("계정을 골라 주세요", ok=False)
    unlink_google(request.app.state.db, _accounts(request), email)
    return _back(f"{email} 연결을 지웠습니다(구글 쪽 권한은 그 계정의 보안 설정에서 따로 지울 수 있습니다)")


# ---------------- 구성원(조직 관리 화면): 같은 부서의 권한 있는 사람이 직원에게 등급·추가 권한을 준다 ----------------
# 권한은 본인이 고르지 않는다(10-22 사용자). 총괄 책임자(부서장)는 자기 부서 직원의 등급(일반·행정 담당자·사업 책임자)과
# 추가 권한을 정하고, 사업 책임자는 자기 부서 일반 직원에게 추가 권한만 준다(등급은 못 바꿈). 총괄 책임자 지정은 관리자가.
TEAM_GRADES = ("member", "clerk", "lead")
LEAD_GRANTABLE = ("create_project",)          # 사업 책임자가 일반 직원에게 줄 수 있는 추가 권한


def _grantable(actor_grade: str) -> tuple[str, ...]:
    """구성원 화면에서 이 사람이 줄 수 있는 추가 권한 — 관리자는 전부, 총괄 책임자는 관리자 전용(SFT 문답 검수)을 뺀 전부,
    사업 책임자는 LEAD_GRANTABLE."""
    if actor_grade == "admin":
        return tuple(grades.PERMS)
    if actor_grade == "head":
        return tuple(k for k in grades.PERMS if k not in grades.ADMIN_ONLY_PERMS)
    return LEAD_GRANTABLE


def _team_actor(request: Request) -> tuple[str, dict, dict, str]:
    """(나, 내 계정, {다룰 부서: 그 부서에서 내 등급}, 대표 등급) — 겸직·부서 위계를 따른다(grades.covered_depts).
    관리자는 빈 dict(모든 부서). 총괄 책임자는 맡은 부서(하위 부서 포함)에서 head, 사업 책임자는 자기가 사업 책임자인 부서에서 lead."""
    accounts = _accounts(request)
    me = _user(request)
    mine = accounts.get(me) or {}
    g = grades.request_grade(request)
    if g == "admin":
        return me, mine, {}, g
    dd = getattr(request.app.state, "data_dir", None)
    if getattr(request.state, "view_as", None):        # 테스트 보기 — 고른 등급을 소속 부서에
        d0 = str(getattr(request.state, "dept", "") or "").strip()
        scope = {d0: g} if d0 and g in ("head", "lead") else {}
    else:
        scope = {d: "head" for d in grades.covered_depts(me, mine, dd, "head")}
        for d in grades.depts_of(me, mine, dd):
            if d not in scope and grades.grade_in(me, mine, d, dd) == "lead":
                scope[d] = "lead"
    if not scope:
        raise HTTPException(403, "구성원 권한은 그 부서의 총괄 책임자·사업 책임자와 관리자가 정합니다")
    return me, mine, scope, ("head" if "head" in scope.values() else "lead")


def _can_touch(actor_grade: str, target_grade: str) -> tuple[bool, bool]:
    """(추가 권한을 바꿀 수 있나, 등급을 바꿀 수 있나)."""
    if actor_grade in ("admin", "head"):
        return target_grade in TEAM_GRADES, target_grade in TEAM_GRADES
    if actor_grade == "lead":
        return target_grade == "member", False
    return False, False


def _member_grade(uid: str, a: dict, dept: str, data_dir=None) -> str:
    """그 부서에서의 등급 — 소속 부서면 계정 등급, 겸직 부서면 부서별 등급(없으면 일반)."""
    return grades.grade_in(uid, a, dept, data_dir) or ""


def _team_rows(accounts: dict, dept: str, actor_grade: str = "admin", data_dir=None) -> list[dict]:
    rows = []
    for uid, a in sorted(accounts.items()):
        if a.get("role") not in grades.STAFF or not a.get("active", True):
            continue
        if dept not in grades.depts_of(uid, a, data_dir):
            continue
        g = _member_grade(uid, a, dept, data_dir)
        primary = str(a.get("dept") or "").strip() == dept
        perm_ok, grade_ok = _can_touch(actor_grade, g)
        rows.append({"uid": uid, "name": a.get("name") or uid, "grade": g, "grade_label": grades.label(g),
                     "primary": primary, "home": "" if primary else str(a.get("dept") or "").strip(),
                     "perms": [k for k in grades.PERMS if k in (a.get("perms") or ())], "editable": perm_ok, "grade_editable": grade_ok,
                     "by": a.get("perms_by") or ""})
    rows.sort(key=lambda r: (not r["primary"], -grades.RANK.get(r["grade"], 0), r["name"]))
    return rows


@router.get("/team", response_class=HTMLResponse)
def team_page(request: Request, dept: str = "", ok: str = "", err: str = "", tab: str = "", view: str = ""):
    """조직 관리 — 왼쪽 부서 트리(맨 위 「전체 사용자」), 오른쪽 고른 것의 상세(구성원·부서 설정 / 계정 목록) 한 화면.
    관리자에게는 아래에 학교 계정 연결 현황(구글 앱 등록·허용 도메인·연결 삭제)도 보인다. 자료는 화면이 /api/org·/api/org/members·/api/org/accounts 로 받고,
    고치기는 그 경로들이 권한을 매번 다시 본다. 교직원 누구나 트리와 부서 개요를 보고, 구성원·설정은 권한만큼만 보인다."""
    st = request.app.state
    from zzaimy.app.org_routes import Actor
    actor = Actor(request)
    admin = getattr(request.state, "role", "") == "dev" and actor.admin
    ctx = {"org_admin": actor.admin, "accounts_ok": admin, "start_dept": " ".join(dept.split()),
           "start_view": "all" if view == "all" and admin else "", "ok": ok, "err": err}
    if admin:                                               # 계정 편집 창(전체 사용자) — 고르기 칸과 등급·권한 이름
        data = overview(st.db, _accounts(request), getattr(st, "data_dir", None))
        ctx.update({k: data[k] for k in ("roles", "grades", "perms", "default_grade", "admin_max", "admin_n", "emails", "domain", "app")})
        ctx["redirect_uri"] = redirect_uri(request)       # Google 앱 등록 창에 보일 되돌아올 주소
        ctx["dept_choices"] = known_depts(st.db, _accounts(request), getattr(st, "data_dir", None))
        ctx["dept_labels"] = dict_candidates(st, ctx["dept_choices"])["dept_labels"]
        ctx["password_rules"] = {"min_length": passwords.MIN_LENGTH}
    return st.templates.TemplateResponse(request, "team.html", st.page_ctx(request, ctx))


@router.get("/api/org/accounts")
def api_org_accounts(request: Request):
    """전체 사용자(관리자만) — 계정 목록 자료. 찾기·거르기·쪽 나누기는 화면이 한다."""
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "관리자만 전체 계정을 봅니다")
    st = request.app.state
    data = overview(st.db, _accounts(request), getattr(st, "data_dir", None))
    keep = ("user", "name", "role", "role_label", "grade", "grade_label", "perms", "dept", "extra", "extra_grades", "active",
            "bound", "bound_ok", "effective", "via")
    return JSONResponse({"users": [{k: u.get(k) for k in keep} for u in data["users"]], "me": _user(request)})


@router.get("/api/org/members")
def api_org_members(request: Request, dept: str = ""):
    """부서 구성원 — 등급·추가 권한과 내가 바꿀 수 있는 것. 그 부서의 총괄 책임자·사업 책임자와 관리자만."""
    me, mine, scope, _ag = _team_actor(request)
    dept = " ".join(dept.split())
    dd = getattr(request.app.state, "data_dir", None)
    if dept not in known_depts(request.app.state.db, _accounts(request), dd) and dept not in scope:
        raise HTTPException(404, "없는 부서입니다")
    if scope and dept not in scope:
        raise HTTPException(403, "맡은 부서의 구성원만 봅니다")
    ag = "admin" if not scope else scope[dept]
    return JSONResponse({"dept": dept, "rows": _team_rows(_accounts(request), dept, ag, dd), "actor_grade": ag,
                         "perms": grades.PERMS, "grantable": list(_grantable(ag)),
                         "grades": {k: grades.GRADES[k] for k in TEAM_GRADES},
                         "can_place": ag in ("admin", "head"),               # 옮기기·빼기·사람 추가
                         "can_primary": ag == "admin",                       # 소속 부서째 바꾸기(사람 추가 「소속으로」·소속 빼기)
                         "targets": sorted(scope) if scope else []})          # 옮길 수 있는 부서(비면 모든 부서)


MEMBER_OPS = ("grade", "perms", "move", "remove", "add")


@router.post("/api/org/members")
async def api_org_members_op(request: Request):
    from starlette.concurrency import run_in_threadpool
    try:
        b = await request.json()
    except Exception:
        b = None
    if not isinstance(b, dict):
        raise HTTPException(400, "요청 형식이 올바르지 않습니다")
    return await run_in_threadpool(_members_op, request, b)


@org_lock.serialized
def _members_op(request: Request, b: dict):
    """구성원 여러 명을 한 번에 — op: grade(value=등급) · perms(value=[권한], 한 사람) · move(value=옮길 부서, 소속 사람) ·
    remove(소속이면 부서 없음으로 — 관리자만, 겸직이면 이 부서 겸직만 푼다) · add(value=primary 소속으로 — 관리자만 | extra 겸직으로).
    권한은 구성원 화면 규칙 그대로(총괄 책임자는 맡은 부서 안에서, 사업 책임자는 일반 직원의 추가 권한만). 저장은 계정 편집과 같은 _save_one.
    응답 {ok, msg, done, failed}."""
    st = request.app.state
    me, _mine, scope, _ = _team_actor(request)
    accounts = _accounts(request)
    dd = getattr(st, "data_dir", None)
    dept = " ".join(str(b.get("dept") or "").split())
    op = str(b.get("op") or "")
    value = b.get("value")
    uids = [str(u).strip().lower() for u in (b.get("uids") or []) if str(u).strip()]
    known = known_depts(st.db, accounts, dd)
    fail = lambda m, code=400: JSONResponse({"ok": False, "msg": m}, status_code=code)
    if op not in MEMBER_OPS:
        return fail("할 일을 골라 주세요")
    if dept not in known and dept not in scope:          # 겸직 등급으로만 남은 부서도 맡은 사람은 다룬다
        return fail("없는 부서입니다")
    if scope and dept not in scope:
        return fail("맡은 부서의 구성원만 바꿉니다", 403)
    ag = "admin" if not scope else scope[dept]
    if not uids:
        return fail("사람을 골라 주세요")
    if op in ("move", "remove", "add") and ag not in ("admin", "head"):
        return fail("옮기기·빼기·사람 추가는 총괄 책임자와 관리자가 합니다", 403)
    if op == "move":
        value = " ".join(str(value or "").split())
        if value not in known or value == dept:
            return fail("옮길 부서를 골라 주세요")
        if scope and value not in scope:
            return fail("맡은 부서 안으로만 옮길 수 있습니다", 403)
    if op == "add" and value not in ("primary", "extra"):
        return fail("소속으로 넣을지 겸직으로 넣을지 골라 주세요")
    if op == "add" and value == "primary" and ag != "admin":
        return fail("다른 부서 사람을 소속으로 옮겨 오는 것은 관리자가 합니다 — 겸직으로 넣을 수 있습니다", 403)
    if op == "grade" and (ag not in ("admin", "head") or value not in TEAM_GRADES):
        return fail("등급(사업 책임자·행정 담당자 지정)은 총괄 책임자가 정합니다", 403 if value in TEAM_GRADES else 400)
    done, failed = [], []
    for uid in dict.fromkeys(uids):
        a = accounts.get(uid)
        if not a or a.get("role") not in grades.STAFF or not a.get("active", True):
            failed.append({"uid": uid, "msg": "바꿀 수 있는 교직원 계정이 아닙니다"})
            continue
        primary = str(a.get("dept") or "").strip()
        extras = _extras(uid, a, dd)
        eg = dict(grades.dept_grades(a))
        member = dept in grades.depts_of(uid, a, dd)
        if op != "add" and not member:
            failed.append({"uid": uid, "msg": "이 부서 사람이 아닙니다"})
            continue
        cur = _member_grade(uid, a, dept, dd)
        perm_ok, grade_ok = _can_touch(ag, cur)
        new_dept, perms = primary, list(a.get("perms") or ())
        if op == "grade":
            if not grade_ok:
                failed.append({"uid": uid, "msg": "이 사람의 등급은 바꿀 수 없습니다"})
                continue
            if primary == dept:
                a["grade"] = value
            else:
                eg[dept] = value
        elif op == "perms":
            if not perm_ok:
                failed.append({"uid": uid, "msg": "이 사람의 추가 권한은 바꿀 수 없습니다"})
                continue
            grantable = set(_grantable(ag))
            want = set(value or [])
            perms = [k for k in grades.PERMS if (k in perms and k not in grantable) or (k in grantable and k in want)]
        elif op == "move":
            if primary != dept:
                failed.append({"uid": uid, "msg": "겸직인 사람은 옮기지 않습니다 — 빼기로 이 부서 겸직을 푸세요"})
                continue
            if not grade_ok and ag != "admin":
                failed.append({"uid": uid, "msg": "총괄 책임자는 관리자가 옮깁니다"})
                continue
            new_dept = value
            extras = [d for d in extras if d != value]
        elif op == "remove":
            if primary == dept:
                if ag != "admin":
                    failed.append({"uid": uid, "msg": "소속을 비우는 것은 관리자가 합니다 — 다른 부서로 옮겨 주세요"})
                    continue
                new_dept = ""
            else:
                extras = [d for d in extras if d != dept]
        elif op == "add":
            if member and not (value == "primary" and primary != dept):
                failed.append({"uid": uid, "msg": "이미 이 부서 사람입니다"})
                continue
            if value == "primary":
                new_dept = dept
                extras = [d for d in extras if d != dept]
            else:
                extras = extras + [dept]
        err = _save_one(st, accounts, me if ag == "admin" else "", uid=uid, new=False, name=a.get("name", ""),
                        role=a.get("role", "staff"), dept=new_dept, on=a.get("active", True), google=None,
                        extra=extras, extra_grade=[eg.get(d, "member") for d in extras],
                        grade=a.get("grade", "") if a.get("grade") in grades.GRADES else "", perm=perms)
        if err:
            failed.append({"uid": uid, "msg": err})
            continue
        if op in ("grade", "perms"):
            accounts[uid]["perms_by"] = me
            st.save_accounts()
        done.append(uid)
    if done and op in ("move", "remove", "add"):
        _sync_drives_later(st, accounts)
    label = {"grade": "등급을 바꿨습니다", "perms": "추가 권한을 저장했습니다", "move": f"「{value}」 로 옮겼습니다",
             "remove": "이 부서에서 뺐습니다", "add": "이 부서에 넣었습니다"}[op]
    msg = f"{len(done)}명 — {label}" + (f" · {len(failed)}명은 바꾸지 못했습니다({failed[0]['msg']})" if failed else "")
    if not done:
        msg = failed[0]["msg"] if failed else "바뀐 것이 없습니다"
    return JSONResponse({"ok": bool(done), "msg": msg, "done": done, "failed": failed}, status_code=200 if done else 400)


# 조직 관리(/team)의 트리 자료·부서 고치기 — org_routes. main.py 가 이 라우터만 붙이므로 여기서 함께 붙인다
from zzaimy.app.org_routes import router as _org_router  # noqa: E402

router.include_router(_org_router)
