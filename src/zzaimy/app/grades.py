"""교직원 등급 — 일반·사업 책임자·총괄 책임자, 그리고 관리자(ADR-0066).

계정 역할(role)은 그대로 교직원(staff)·학생(student)·관리자(dev) 셋이고, 교직원 안에서 등급(grade)을 나눈다.
- 행정 담당자(clerk): 사업의 행정 지원. 사업 책임자와 같은 단계(프로젝트 만들기, 맡은 사업 진행 현황).
- 일반(member): 권한을 받아야 작업에 참여한다 — 공유받은 프로젝트에서만 작업, 새 프로젝트는 만들지 않는다. 질문·검색은 지금처럼.
- 사업 책임자(lead): 세부 사업 책임자. 프로젝트를 만들고 진행하며, 자기가 책임자인 프로젝트·사업의 진행 현황을 본다.
- 총괄 책임자(head): 부서장. 자기 부서(와 맡은 사업)의 프로젝트 여럿을 총괄 책임자 — 그 진행 현황을 모두 보고 사업 책임자를 정한다.
- 관리자(role=dev): 개발자와 시스템 전체 관리자 — 모두 볼 수 있고 사업 관리(전체 사업 장부)를 맡는다. 최대 ADMIN_MAX 명.
등급이 없는 기존 교직원 계정은 사업 책임자로 본다(이미 프로젝트를 만들어 쓰던 사람이 갑자기 막히지 않게).
RAG 공간에도 필요하면 최소 등급을 걸 수 있다(rag_spaces 설정 min_grade) — 기본은 걸지 않음.
사업 관리(program_admin)에서 사업마다 총괄 책임자·사업 책임자를 지정하면 그 사업에 이어진 프로젝트의 진행 현황 권한으로 이어진다.
"""
from __future__ import annotations

GRADES = {"member": "일반", "clerk": "행정 담당자", "lead": "사업 책임자", "head": "총괄 책임자"}
RANK = {"member": 1, "clerk": 2, "lead": 2, "head": 3, "admin": 9}   # 행정 담당자는 사업 책임자와 같은 단계(프로젝트 만들기·사업 행정 지원)
DEFAULT_GRADE = "lead"
ADMIN_MAX = 2
STAFF = ("staff", "professor", "head")          # access_policy.STAFF_ROLES 와 같은 뜻(예전 역할 포함)
COMMON_SPACE = "common"
# 등급과 따로 계정마다 더 주는 권한(사용자·계정 화면) — 일반 직원에게 프로젝트 만들기만 열어 주는 식으로 쓴다
PERMS = {"create_project": "프로젝트 만들기", "view_activity": "부서 프로젝트 진행 현황 보기"}


def has_perm(account: dict | None, key: str) -> bool:
    """계정에 더 준 권한 — 관리자는 모두, 학생은 없음."""
    a = account or {}
    if a.get("role") == "dev":
        return True
    if (a.get("role") or "") not in STAFF:
        return False
    return key in (a.get("perms") or ())                         # RAG 최소 등급의 공통 RAG 열쇠 — 부서 RAG 는 「dept:<부서>」


def grade_of(account: dict | None) -> str:
    """계정 하나의 등급 — 관리자는 admin, 학생·모르는 역할은 빈 값."""
    a = account or {}
    role = a.get("role") or ""
    if role == "dev":
        return "admin"
    if role not in STAFF:
        return ""
    g = a.get("grade") or ""
    return g if g in GRADES else DEFAULT_GRADE


def user_grade(account: dict | None, view_as: dict | None = None) -> str:
    """지금 판정에 쓰는 등급 — 개발자 「테스트 보기」 중이면 고른 역할·등급(교직원이면 고른 등급, 안 고르면 기본값)."""
    if view_as:
        if view_as.get("role") not in STAFF:
            return ""
        g = view_as.get("grade") or ""
        return g if g in GRADES else DEFAULT_GRADE
    return grade_of(account)


def request_grade(request) -> str:
    """요청의 등급 — 로그인 검사가 request.state 에 둔 계정·테스트 보기로. 인증 없는 로컬 개발 모드(계정 표 밖)는 관리자."""
    st = request.state
    user = getattr(st, "user", "") or ""
    accounts = getattr(request.app.state, "accounts", None) or {}
    va = getattr(st, "view_as", None)
    if va:
        return user_grade(None, va)
    acct = {**(accounts.get(user) or {}), "role": getattr(st, "role", "") or ""}
    return top_grade(user, acct, getattr(request.app.state, "data_dir", None))     # 겸직 부서의 등급 가운데 가장 높은 것


def label(grade: str) -> str:
    return "관리자" if grade == "admin" else GRADES.get(grade, "")


def at_least(grade: str, need: str) -> bool:
    return RANK.get(grade, 0) >= RANK.get(need, 99)


# ---------------------------------------------------------------- 겸직(여러 부서)과 부서 위계
# 한 사람이 여러 부서에 속할 수 있다(소속 부서 + 추가 부서 — rag_spaces grants). 부서마다 등급을 따로 줄 수 있다
# (account["dept_grades"] = {"○○사업단": "head"}) — 기획처에선 일반, ○○사업단에선 총괄 책임자 같은 겸직. 따로 정하지 않은 추가 부서는 일반.
# 부서 위계(산학협력단이 ○○사업단을 관리)는 dept_dictionary.parent_of 로 — 상위 부서의 총괄 책임자는 하위 부서도 맡는다.


def dept_grades(account: dict | None) -> dict:
    a = account or {}
    if (a.get("role") or "") not in STAFF:
        return {}
    return {str(d).strip(): g for d, g in (a.get("dept_grades") or {}).items() if str(d).strip() and g in GRADES}


def depts_of(user: str, account: dict | None, data_dir=None) -> list[str]:
    """이 사람이 속한 부서 — 소속 부서 먼저, 그다음 추가 부서(겸직)·부서별 등급을 받은 부서."""
    a = account or {}
    out = [str(a.get("dept") or "").strip()] if str(a.get("dept") or "").strip() else []
    try:
        from zzaimy.app import access_policy
        out += [d for d in access_policy.extra_depts(user, data_dir) if d not in out]
    except Exception:
        pass
    out += [d for d in dept_grades(a) if d not in out]
    return out


def grade_in(user: str, account: dict | None, dept: str, data_dir=None) -> str:
    """그 부서에서의 등급 — 관리자는 admin, 소속 부서는 계정 등급, 다른 부서는 부서별 등급(없으면 일반, 속하지 않으면 빈 값)."""
    g = grade_of(account)
    if g in ("admin", ""):
        return g
    dept = (dept or "").strip()
    if not dept:
        return ""
    own = dept_grades(account).get(dept)
    if own:
        return own if RANK.get(own, 0) >= RANK.get(g, 0) or dept != str((account or {}).get("dept") or "").strip() else g
    if dept == str((account or {}).get("dept") or "").strip():
        return g
    return "member" if dept in depts_of(user, account, data_dir) else ""


def _children(data_dir=None) -> dict:
    try:
        from zzaimy.app import dept_dictionary
        return dept_dictionary.children_map(data_dir) or {}
    except Exception:
        return {}


def covered_depts(user: str, account: dict | None, data_dir=None, need: str = "head") -> set[str]:
    """need 등급 이상으로 맡는 부서 — 부서별 등급과 부서 위계(하위 부서까지)를 따른다. 관리자는 빈 집합(전부라는 뜻은 호출부가)."""
    roots = {d for d in depts_of(user, account, data_dir) if at_least(grade_in(user, account, d, data_dir), need)}
    kids = _children(data_dir)
    out, stack = set(), list(roots)
    while stack:
        d = stack.pop()
        if d in out:
            continue
        out.add(d)
        stack += list(kids.get(d) or ())
    return out


def top_grade(user: str, account: dict | None, data_dir=None) -> str:
    """가장 높은 등급(겸직 포함) — 화면 메뉴(사업 관리·부서 사람)를 보일지 정할 때."""
    g = grade_of(account)
    if g in ("admin", ""):
        return g
    best = g
    for d in depts_of(user, account, data_dir):
        x = grade_in(user, account, d, data_dir)
        if RANK.get(x, 0) > RANK.get(best, 0):
            best = x
    return best


def can_create_project(account: dict | None) -> bool:
    """새 프로젝트를 만들 수 있는가 — 사업 책임자 이상(겸직 부서의 등급 포함), 또는 「프로젝트 만들기」 권한을 받은 계정."""
    return (at_least(grade_of(account), "lead") or has_perm(account, "create_project")
            or any(at_least(x, "lead") for x in dept_grades(account).values()))


def can_view_activity(project: dict | None, *, user: str, account: dict | None, lead: str = "",
                      project_dept: str = "", program_roles: dict | None = None, data_dir=None) -> bool:
    """프로젝트 진행 현황(참여자·요청·토큰·작업 요약)을 볼 수 있는가.
    관리자 전부, 총괄 책임자는 자기 부서 프로젝트와 자기가 총괄 책임자로 지정된 사업의 프로젝트, 사업 책임자는 자기가 만든·책임자로 지정된
    프로젝트와 자기가 사업 책임자로 지정된 사업의 프로젝트. 일반은 보지 않는다.
    program_roles: 그 프로젝트가 이어진 사업의 지정 {"heads": [...], "leads": [...]} — 주지 않으면 사업 관리 장부
    (program_admin.roles_for_project, data_dir)에서 찾는다. 사업에 이어지지 않은 프로젝트는 찾지 않는다."""
    if not project:
        return False
    g = grade_of(account)
    if g == "admin":
        return True
    pd = (project_dept or "").strip()
    if user and pd and pd in covered_depts(user, account, data_dir):     # 그 부서(또는 상위 부서)의 총괄 책임자 — 겸직 포함
        return True
    tg = top_grade(user, account, data_dir) if user else g
    if g == "member" and tg != "member":                 # 소속에선 일반이어도 겸직 부서에서 사업 책임자·행정 담당자면 그 갈래로
        g = tg if tg != "head" else "lead"
    if g == "member" and has_perm(account, "view_activity") and user:   # 일반이어도 권한을 받으면 자기 부서 프로젝트
        mine = str((account or {}).get("dept") or "").strip()
        return bool(mine) and mine == (project_dept or "").strip() or user in (project.get("owner"), lead)
    if g not in ("head", "lead", "clerk") or not user:
        return False
    if user in (project.get("owner"), lead):
        return True
    if g == "head" or has_perm(account, "view_activity"):
        mine = str((account or {}).get("dept") or "").strip()
        if mine and mine == (project_dept or "").strip():
            return True
    roles = program_roles
    if roles is None and (project.get("program") or "").strip():
        try:
            from zzaimy.app import program_admin
            roles = program_admin.roles_for_project(project, data_dir)
        except Exception:
            roles = None
    roles = roles or {}
    if user in (roles.get("leads") or ()) or user in (roles.get("clerks") or ()):
        return True
    heads = roles.get("heads") or ()
    if g == "head" and user in heads:
        return True
    # 사업의 총괄 책임자로 부서가 지정됐으면(「dept:산학협력단」) 그 부서를 맡는 총괄 책임자(겸직·상위 부서 포함)도
    dept_heads = {str(h)[5:].strip() for h in heads if str(h).startswith("dept:")}
    return bool(dept_heads) and bool(dept_heads & covered_depts(user, account, data_dir))


def admin_count(accounts: dict, *, except_uid: str = "") -> int:
    return sum(1 for u, a in accounts.items() if u != except_uid and a.get("role") == "dev" and a.get("active", True))


def admin_over(accounts: dict, uid: str, role: str, active: bool) -> bool:
    """이 계정을 활성 관리자로 두면 상한(ADMIN_MAX)을 넘는가 — 이미 활성 관리자인 계정을 그대로 두는 저장은 넘지 않는다."""
    if role != "dev" or not active:
        return False
    return admin_count(accounts, except_uid=uid) >= ADMIN_MAX


def rag_allowed(space: str, account: dict | None, cfg: dict) -> bool:
    """RAG 공간에 최소 등급이 걸려 있으면 그 이상만 — rag_spaces 설정 {"min_grade": {"common": "lead", "dept:기획처": "head"}}.
    걸려 있지 않으면 지금 규칙(부서·추가 권한) 그대로. 관리자는 늘 허용."""
    need = ((cfg or {}).get("min_grade") or {}).get(space)
    if not need:
        return True
    return at_least(grade_of(account), need)


def rag_blocked(cfg: dict | None, grade: str) -> set[str]:
    """이 등급이 닿지 못하는 RAG 의 문서 부서 이름(공통 RAG 는 「공통」). 등급이 빈 값(걸지 않음·학생)이거나 관리자면 빈 집합 —
    걸린 것이 없으면 늘 빈 집합이라 지금 동작과 같다."""
    need = (cfg or {}).get("min_grade") or {}
    if not need or not grade or grade == "admin":
        return set()
    out = set()
    for space, g in need.items():
        if not g or at_least(grade, g):
            continue
        if space == COMMON_SPACE:
            out.add("공통")
        elif str(space).startswith("dept:") and str(space)[5:].strip():
            out.add(str(space)[5:].strip())
    return out


def program_scope(account: dict | None) -> str:
    """사업 관리 화면을 어디까지 보나 — all(관리자: 전부·편집) · dept(총괄 책임자: 자기 부서·총괄 책임자 지정 사업 읽기, 책임자 지정) ·
    own(사업 책임자: 자기가 책임자인 사업 읽기) · 빈 값(일반·학생: 열지 못함)."""
    g = grade_of(account)
    for x in dept_grades(account).values():              # 겸직 부서에서의 등급도(가장 높은 것)
        if g != "admin" and RANK.get(x, 0) > RANK.get(g, 0):
            g = x
    return {"admin": "all", "head": "dept", "lead": "own", "clerk": "own"}.get(g, "")
