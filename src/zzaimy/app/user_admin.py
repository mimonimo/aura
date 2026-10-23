"""사용자 관리와 사용자별 구글 계정 연결.

구글 API(앱 등록, OAuth 클라이언트)는 개발자 계정이 한 번 해 두고, 관리자가 플랫폼 계정을 만들면 사용자마다 자기 학교 계정
(ync.ac.kr)을 연결한다. 구글 허용은 그 계정 주인이 로그인해야 하므로 본인만 할 수 있다(설정 화면의 '내 구글 계정'). 관리자는
/dev/users 에서 계정을 만들고(역할·부서·사용 여부·비밀번호 초기화), 누가 어느 구글 계정에 이어졌는지 보고, 이음을 풀거나 이미
연결된 계정으로 다시 지정하고, 허용 도메인·부서 공용 계정·부서 공유 드라이브를 정한다. 문서 작성은 각자 연결한 학교 계정으로
한다.

- 사용자: GET /account/google(상태 JSON), GET /account/google/connect(허용 화면으로), POST /account/google/disconnect
- 관리자(/dev 는 개발자 역할만 — main.py 의 check_auth): GET /dev/users, POST /dev/users/save(역할·교직원 등급·부서 —
  등급과 관리자 상한은 grades, ADR-0066),
  POST /dev/google/{settings,dept,revoke}, POST /dev/users/dept(부서 만들기·지우기·사전에서 여러 개 만들기 create_many),
  POST /dev/users/dept-parent(상위 부서 — 관리자가 확인한 관계만 권한에 쓰인다, dept_dictionary). 계정의 겸직 부서는
  줄마다 부서 + 그 부서에서의 등급(rag grants dept: 와 account["dept_grades"] 를 함께 맞춘다)
- 부서 사람: GET /team(등급·추가 권한), GET /team?tab=org(조직도 — 자료·고치기는 org_routes, 이 라우터에 함께 붙는다)

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


def _extras(uid: str, a: dict, data_dir=None) -> list[str]:
    """겸직 부서 — RAG 추가 권한(grants dept:)과 부서별 등급(dept_grades)을 합친 것(소속 부서 빼고)."""
    from zzaimy.app import access_policy
    out = list(access_policy.extra_depts(uid, data_dir)) if data_dir else []
    out += [d for d in grades.dept_grades(a) if d not in out]
    return [d for d in out if d and d != str(a.get("dept") or "").strip()]


def overview(db, accounts: dict, data_dir=None) -> dict:
    conn = connected_emails()
    users = []
    from zzaimy.app import access_policy
    for uid, a in sorted(accounts.items(), key=lambda kv: (not kv[1].get("active", True), kv[1].get("dept") or "", kv[0])):
        st = user_status(db, uid, a.get("dept") or None)
        g = grades.grade_of(a)
        users.append({**st, "name": a.get("name", ""), "role": a.get("role", "staff"), "role_label": ROLES.get(a.get("role", "staff"), ""),
                      "grade": g if g in grades.GRADES else "", "grade_label": grades.GRADES.get(g, ""),
                      "perms": [k for k in grades.PERMS if k in (a.get("perms") or ())] if a.get("role") in grades.STAFF else [],
                      "dept": a.get("dept", ""), "extra": (ex := _extras(uid, a, data_dir)),
                      "extra_grades": [grades.dept_grades(a).get(d, "member") for d in ex],
                      "active": a.get("active", True), "created_at": (a.get("created_at") or "")[:10]})
    settings = db.all_settings()
    # 부서 목록은 한 곳(known_depts) — 여기서 만든 부서가 RAG 공간의 부서 RAG·검색 범위·폴더 범위에 그대로 쓰인다
    depts = known_depts(db, accounts, data_dir)
    n_docs: dict[str, int] = {}
    if depts:
        with db._conn() as c:
            n_docs = {str(r[0]): int(r[1]) for r in c.execute(
                f"SELECT dept, COUNT(*) FROM documents WHERE dept IN ({','.join('?' * len(depts))}) GROUP BY dept", depts)}
    from zzaimy.ingest import dept_drive
    from zzaimy.app import dept_dictionary
    dic = dept_dictionary.load(data_dir)
    confirmed = dept_dictionary.parents_override(data_dir)
    suggested = dept_dictionary.suggested_parents(data_dir)
    dept_rows = [{"dept": d, "account": (settings.get(f"google_account_dept:{d}") or "").strip(),
                  "line": dic.line(d) if dic.normalize(d) else d,
                  "parent": confirmed.get(d, ""),
                  # 규정에서 뽑은 상위(제안) — 아직 관리자가 정하지 않았고 그 상위가 부서로 있을 때만
                  "suggested": (sp if d not in confirmed and (sp := suggested.get(dic.normalize(d) or d, "")) in depts else ""),
                  "root": (settings.get(f"google_root:{d}") or "").strip(),
                  "drive": dept_drive.state(db, d, accounts, data_dir),
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

def _back(msg: str, ok: bool = True) -> RedirectResponse:
    return RedirectResponse(f"/dev/users?{'ok' if ok else 'err'}={quote(msg)}", status_code=303)


@router.get("/dev/users", response_class=HTMLResponse)
def dev_users(request: Request, ok: str = "", err: str = ""):
    st = request.app.state
    data = overview(st.db, _accounts(request), getattr(st, "data_dir", None))
    data["me"] = _user(request)
    data["dept_choices"] = known_depts(st.db, _accounts(request), getattr(st, "data_dir", None))
    data.update(dict_candidates(st, data["dept_choices"]))
    return st.templates.TemplateResponse(request, "dev_users.html", st.page_ctx(request, {**data, "ok": ok, "err": err}))


def dict_candidates(st, existing: list[str]) -> dict:
    """부서 사전(dept_dictionary)에서 고를 후보 — 만들 수 있는 것(행정·사업 조직, 이미 있는 부서 제외)과 건너뛴 까닭.
    화면은 이것을 「사전에서 고르기」 목록·부서 이름 자동완성·계정 부서 목록의 보조 후보로 쓴다. 자동으로 만들지는 않는다."""
    from zzaimy.app import dept_dictionary
    d = dept_dictionary.current(getattr(st, "db", None), getattr(st, "data_dir", None))
    plan = dept_dictionary.plan_create(d, existing)
    labels = {x: d.line(x) for x in existing if d.normalize(x)}
    return {"dict_ready": bool(d.units), "dict_create": [d.view(d.by_name[n]) for n in plan["create"]],
            "dict_skipped": len(plan["skip"]), "dept_labels": labels}


@router.get("/dev/google")
def dev_google_moved():
    return RedirectResponse("/dev/users", status_code=301)


@router.post("/dev/users/save")
@org_lock.serialized                                   # 조직·계정 저장값 공용 잠금(org_lock)
def dev_users_save(request: Request, uid: str = Form(""), is_new: str = Form(""), name: str = Form(""),
                   role: str = Form("staff"), dept: str = Form(""), active: str = Form(""), new_pw: str = Form(""),
                   google: str = Form(""), extra: list[str] = Form([]), grade: str = Form(""),
                   perm: list[str] = Form([]), extra_grade: list[str] = Form([])):
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
    # 관리자는 개발자와 시스템 전체 관리자 둘까지(ADR-0066) — 이미 사용 중인 관리자 계정을 그대로 고쳐 저장하는 것은 막지 않는다
    was_admin = not new and accounts[uid].get("role") == "dev" and accounts[uid].get("active", True)
    if not was_admin and grades.admin_over(accounts, uid, role, on):
        return _back(f"관리자는 최대 {grades.ADMIN_MAX}명까지 둘 수 있습니다 — 다른 관리자 계정을 교직원으로 바꾸거나 사용 중지한 뒤 지정해 주세요",
                     ok=False)
    grade = grade.strip()
    if grade and grade not in grades.GRADES:
        return _back("등급 값이 올바르지 않습니다", ok=False)
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
    # 겸직 부서(여러 줄: 부서 + 그 부서에서의 등급) — RAG 추가 권한(grants dept:)과 부서별 등급(dept_grades) 둘 다 맞춘다.
    # 교직원만 받는다(학생·관리자는 비운다). 소속 부서·모르는 부서는 빼고, 일반은 따로 적지 않는다(없으면 일반)
    data_dir = getattr(st, "data_dir", None)
    known = set(known_depts(st.db, accounts, data_dir))
    pairs: dict[str, str] = {}
    if role == "staff":
        for i, d in enumerate(extra):
            d = d.strip()
            g = (extra_grade[i] if i < len(extra_grade) else "").strip() or "member"
            if d and d in known and d != dept and g in grades.GRADES:
                pairs[d] = g
    dg = {d: g for d, g in pairs.items() if g != "member"}
    if dg:
        accounts[uid]["dept_grades"] = dg
    else:
        accounts[uid].pop("dept_grades", None)
    st.save_accounts()
    if data_dir is not None:
        from zzaimy.app import rag_spaces
        want = sorted(pairs)
        cfg = rag_spaces.config(data_dir)
        grants = dict(cfg.get("grants", {}))
        if sorted(s[5:] for s in grants.get(uid, []) if str(s).startswith("dept:")) != want:
            others = [s for s in grants.get(uid, []) if not str(s).startswith("dept:")]
            grants[uid] = others + [f"dept:{d}" for d in want]
            cfg["grants"] = {u: v for u, v in grants.items() if v}
            rag_spaces.save_config(cfg, data_dir)
    # 부서 드라이브 권한 — 이 사람이 들고 난 부서들을 뒤에서 맞춘다(소속·추가 부서가 바뀌었거나 계정을 껐을 때)
    import threading
    from zzaimy.ingest import dept_drive
    touched = {d for d in known_depts(st.db, accounts, data_dir)
               if (st.db.get_setting(f"google_root_by:{d}", "") or "").strip()}
    threading.Thread(target=lambda: [dept_drive.sync(st.db, d, accounts, data_dir) for d in sorted(touched)],
                     name="dept-drive-sync", daemon=True).start()
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
def dev_google_dept(request: Request, dept: str = Form(""), root: str = Form(""), action: str = Form("connect")):
    """부서 드라이브 연결 — 고른 위치(내 드라이브·공유 드라이브의 폴더) 아래에 「AURA · <부서>」 폴더를 새로 만들고, 그 부서 사람의
    학교 구글 계정에만 편집 권한을 준다(ingest/dept_drive). 연결 계정은 지금 로그인한 관리자의 학교 계정 — 그 계정이 폴더를 만든다.
    action=sync 는 권한만 다시 맞춘다."""
    from zzaimy.ingest import dept_drive
    st = request.app.state
    db = st.db
    dept = dept.strip()
    if not dept or dept not in known_depts(db, _accounts(request), getattr(st, "data_dir", None)):
        return _back("부서를 목록에서 골라 주세요", ok=False)
    if action == "sync":
        got = dept_drive.sync(db, dept, _accounts(request), getattr(st, "data_dir", None))
        return _back(f"부서 {dept} 드라이브 권한을 맞췄습니다 — 줌 {got['added']} · 거둠 {got['removed']}"
                     + (f" ({got['error']})" if got["error"] else ""), ok=not got["error"])
    email = (db.get_setting(f"google_account:{_user(request)}", "") or "").strip()
    if not email or email not in connected_emails():
        return _back("먼저 본인의 학교 계정을 연결해 주세요 — 그 계정이 부서 폴더를 만듭니다", ok=False)
    m = _FOLDER_ID.search(root.strip()) if root.strip() else None
    parent = (m.group(1) or m.group(2)) if m else "root"
    from zzaimy.ingest.folder_picker import folder as check_folder
    if parent != "root":
        try:
            if not check_folder(email, parent)["writable"]:
                return _back("고른 위치에 폴더를 만들 권한이 없습니다", ok=False)
        except (ValueError, httpx.HTTPError):
            return _back("고른 위치를 확인하지 못했습니다. 계정 연결과 쓰기 권한을 확인해 주세요", ok=False)
    try:
        dept_drive.connect(db, dept, email, parent)
    except Exception as e:
        return _back(str(e)[:200], ok=False)
    got = dept_drive.sync(db, dept, _accounts(request), getattr(st, "data_dir", None))
    s_ = dept_drive.state(db, dept, _accounts(request), getattr(st, "data_dir", None))
    msg = f"부서 {dept} 드라이브를 연결했습니다 — 「{dept_drive.folder_name(dept)}」 폴더, 접근 {s_['granted']}명"
    if s_["unlinked"]:
        msg += f" · 구글 미연결 {len(s_['unlinked'])}명({', '.join(s_['unlinked'][:5])}) — 연결하면 다음 맞추기에서 받습니다"
    if got["error"]:
        msg += f" ({got['error']})"
    return _back(msg, ok=not got["error"])


@org_lock.serialized
def delete_dept(st, accounts: dict, name: str) -> tuple[str, int]:
    """부서 하나를 지운다 — (오류 문장, 공통 RAG 로 돌린 문서 수). 소속 계정이 있으면 지우지 않는다.
    부서 RAG 문서는 공통으로, 추가 권한·폴더 짝·부서 공용 설정을 풀고, 겸직 등급·확정 위계에서도 뺀다.
    사용자·계정 화면과 조직도(org_routes)가 같은 이 함수를 쓴다."""
    from zzaimy.app import dept_dictionary, rag_spaces
    data_dir = getattr(st, "data_dir", None)
    name = " ".join((name or "").split())
    if any((a.get("dept") or "").strip() == name for a in accounts.values()):
        return f"「{name}」 부서 계정이 있어 지울 수 없습니다 — 먼저 사람을 다른 부서로 옮기세요", 0
    if name not in known_depts(st.db, accounts, data_dir):
        return "없는 부서입니다", 0
    new, moved = rag_spaces.delete_dept_rag(st.db, rag_spaces.config(data_dir), name)
    rag_spaces.save_config(new, data_dir)
    for k in (f"google_account_dept:{name}", f"google_root:{name}", f"google_root_name:{name}"):
        st.db.set_setting(k, "")
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
    try:                                                 # 남은 흔적(RAG 최소 등급·사업 관리 dept: 지정·부서 공용 설정 등)도 함께
        from zzaimy.app.org_routes import purge_traces
        purge_traces(st, name)
    except Exception:
        pass
    return "", moved


@router.post("/dev/users/dept")
@org_lock.serialized                                   # 조직·계정 저장값 공용 잠금(org_lock)
def dev_users_dept(request: Request, action: str = Form(""), name: str = Form(""), names: list[str] = Form([])):
    """부서 만들기·지우기 — 부서 목록은 RAG 공간 설정(dept_rags)에 하나로 둔다. 만든 부서는 부서 RAG·검색 범위·폴더 범위·계정 부서에서
    바로 고를 수 있다. 지우면 그 부서 문서는 공통 RAG 로 돌아가고 추가 권한·폴더 짝·부서 공용 설정도 풀린다(계정이 있으면 못 지운다)."""
    from zzaimy.app import rag_spaces
    st = request.app.state
    data_dir = getattr(st, "data_dir", None)
    cfg = dict(rag_spaces.config(data_dir))
    name = " ".join((name or "").split())
    from zzaimy.app import dept_dictionary
    dic = dept_dictionary.load(data_dir)
    if action == "create_many":
        # 「사전에서 고르기」 — 관리자가 고른 사전 이름만 만든다(이미 있는 부서·별칭이 같은 부서는 건너뜀). 만드는 길은 하나씩 만들기와 같다
        plan = dept_dictionary.plan_create(dic, known_depts(st.db, _accounts(request), data_dir), [n for n in names if n.strip()])
        made = []
        for n in plan["create"]:
            cfg, err = rag_spaces.create_dept_rag(cfg, n, by=_user(request))
            if not err:
                made.append(n)
        if not made:
            return _back("만들 부서가 없습니다 — 고른 부서가 이미 있거나 사전에서 만들 수 없는 갈래입니다", ok=False)
        rag_spaces.save_config(cfg, data_dir)
        msg = f"부서 {len(made)}개를 만들었습니다: {', '.join(made[:8])}{' …' if len(made) > 8 else ''}"
        if plan["skip"]:
            msg += f" · 건너뜀 {len(plan['skip'])}개"
        return RedirectResponse(f"/dev/users?ok={quote(msg)}#depts", status_code=303)
    if action == "create":
        name = dic.normalize(name) or name             # 별칭(옛 이름·줄임말)으로 적어도 사전의 정식 이름으로
        new, err = rag_spaces.create_dept_rag(cfg, name, by=_user(request))
        if err:
            return _back(err, ok=False)
        rag_spaces.save_config(new, data_dir)
        return RedirectResponse(f"/dev/users?ok={quote(f'부서 「{name}」을 만들었습니다 — 계정 부서·부서 RAG·검색 범위에서 고를 수 있습니다')}#depts",
                                status_code=303)
    if action == "delete":
        err, moved = delete_dept(st, _accounts(request), name)
        if err:
            return _back(err, ok=False)
        return RedirectResponse(f"/dev/users?ok={quote(f'부서 「{name}」을 지웠습니다 · 문서 {moved}건은 공통 RAG 로')}#depts", status_code=303)
    return _back("알 수 없는 요청", ok=False)


@router.post("/dev/users/dept-parent")
@org_lock.serialized                                   # 조직·계정 저장값 공용 잠금(org_lock)
def dev_users_dept_parent(request: Request, dept: str = Form(""), parent: str = Form("")):
    """부서의 상위 부서를 정한다(관리자만) — 상위 부서의 총괄 책임자가 하위 부서도 맡는 권한 판정에 쓰이는 확정 관계.
    비우면 상위 없음. 둘 다 부서 목록에 있어야 하고, 순환(하위를 상위로)은 막는다. 이전 판은 history/ 에 남는다."""
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "관리자만 상위 부서를 정합니다")
    from zzaimy.app import dept_dictionary
    st = request.app.state
    data_dir = getattr(st, "data_dir", None)
    known = known_depts(st.db, _accounts(request), data_dir)
    dept, parent = " ".join(dept.split()), " ".join(parent.split())
    if dept not in known or (parent and parent not in known):
        return _back("부서와 상위 부서는 부서 목록에서 골라 주세요", ok=False)
    if data_dir is None:
        return _back("설정 위치가 없습니다", ok=False)
    err = dept_dictionary.set_parent(data_dir, dept, parent)
    if err:
        return _back(err, ok=False)
    msg = f"「{dept}」 의 상위 부서를 「{parent}」 로 정했습니다" if parent else f"「{dept}」 의 상위 부서를 비웠습니다"
    return RedirectResponse(f"/dev/users?ok={quote(msg)}#depts", status_code=303)


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


# ---------------- 부서 사람: 같은 부서의 권한 있는 사람이 직원에게 등급·추가 권한을 준다 ----------------
# 권한은 본인이 고르지 않는다(10-22 사용자). 총괄 책임자(부서장)는 자기 부서 직원의 등급(일반·행정 담당자·사업 책임자)과
# 추가 권한을 정하고, 사업 책임자는 자기 부서 일반 직원에게 추가 권한만 준다(등급은 못 바꿈). 총괄 책임자 지정은 관리자가.
TEAM_GRADES = ("member", "clerk", "lead")
LEAD_GRANTABLE = ("create_project",)          # 사업 책임자가 일반 직원에게 줄 수 있는 추가 권한


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
        raise HTTPException(403, "부서 사람 권한은 그 부서의 총괄 책임자·사업 책임자와 관리자가 정합니다")
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
def team_page(request: Request, dept: str = "", ok: str = "", err: str = "", tab: str = ""):
    """부서 사람(등급·추가 권한)과 조직도(tab=org) — 조직도는 교직원 누구나 보고, 고치기는 org_routes 가 권한을 다시 본다."""
    st = request.app.state
    if tab == "org":
        from zzaimy.app.org_routes import Actor
        actor = Actor(request)
        try:
            _team_actor(request)
            team_ok = True
        except HTTPException:
            team_ok = False
        return st.templates.TemplateResponse(request, "team.html", st.page_ctx(request, {
            "tab": "org", "team_ok": team_ok, "org_admin": actor.admin, "org_can": actor.can_any, "rows": [], "dept": "",
            "depts": [], "ok": ok, "err": err}))
    me, _, scope, _ag = _team_actor(request)
    dd = getattr(st, "data_dir", None)
    choices = known_depts(st.db, _accounts(request), dd) if not scope else sorted(scope)
    dept = dept if dept in choices else (choices[0] if choices else "")
    ag = "admin" if not scope else scope.get(dept, "lead")
    return st.templates.TemplateResponse(request, "team.html", st.page_ctx(request, {
        "tab": "team", "team_ok": True,
        "rows": _team_rows(_accounts(request), dept, ag, dd), "dept": dept, "actor_grade": ag, "lead_grantable": LEAD_GRANTABLE, "depts": choices, "me": me,
        "perms": grades.PERMS, "team_grades": {k: grades.GRADES[k] for k in TEAM_GRADES}, "ok": ok, "err": err}))


@router.post("/team/save")
@org_lock.serialized                                   # 조직·계정 저장값 공용 잠금(org_lock)
def team_save(request: Request, uid: str = Form(...), grade: str = Form(""), perm: list[str] = Form([]), dept: str = Form("")):
    st = request.app.state
    me, _, scope, _ag = _team_actor(request)
    accounts = _accounts(request)
    dd = getattr(st, "data_dir", None)
    a = accounts.get(uid.strip().lower())
    dept = (dept or "").strip() or str((a or {}).get("dept") or "").strip()
    back = lambda m, good=True: RedirectResponse(f"/team?dept={quote(dept)}&{'ok' if good else 'err'}={quote(m)}", status_code=303)
    if not a or a.get("role") not in grades.STAFF or not a.get("active", True):
        return back("바꿀 수 있는 교직원 계정이 아닙니다", False)
    if dept not in grades.depts_of(uid.strip().lower(), a, dd):
        return back("이 부서에 속한 사람이 아닙니다", False)
    if scope and dept not in scope:
        raise HTTPException(403, "맡은 부서가 아닌 곳의 권한은 바꿀 수 없습니다")
    ag = "admin" if not scope else scope[dept]
    cur = _member_grade(uid.strip().lower(), a, dept, dd)
    perm_ok, grade_ok = _can_touch(ag, cur)
    if not perm_ok:
        return back("이 사람의 권한은 바꿀 수 없습니다 — 사업 책임자는 일반 직원의 추가 권한만, 등급은 총괄 책임자가 정합니다", False)
    if grade and grade != cur:
        if not grade_ok or grade not in TEAM_GRADES:
            return back("등급(사업 책임자·행정 담당자 지정)은 총괄 책임자가 정합니다", False)
        if str(a.get("dept") or "").strip() == dept:
            a["grade"] = grade
        else:                                            # 겸직 부서의 등급은 그 부서에만(소속 부서 등급은 그대로)
            a.setdefault("dept_grades", {})[dept] = grade
    # 사업 책임자는 자기가 가진 권한만 나눠 준다(프로젝트 만들기) — 부서 전체 진행 현황 보기처럼 자기에게 없는 권한은 줄 수도 뺄 수도 없다
    grantable = set(grades.PERMS) if ag in ("admin", "head") else set(LEAD_GRANTABLE)
    keep = {k for k in (a.get("perms") or ()) if k not in grantable}
    a["perms"] = [k for k in grades.PERMS if k in keep or (k in grantable and k in set(perm))]
    if not a["perms"]:
        a.pop("perms", None)
    a["perms_by"] = me
    a["updated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    st.save_accounts()
    return back(f"{a.get('name') or uid} 님의 권한을 저장했습니다")


# 조직도(/team?tab=org 의 자료·고치기 — org_routes). main.py 가 이 라우터만 붙이므로 여기서 함께 붙인다
from zzaimy.app.org_routes import router as _org_router  # noqa: E402

router.include_router(_org_router)
