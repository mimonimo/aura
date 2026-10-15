"""반입 시점의 접근 범위 규칙 — 문서마다 부서(dept)와 열람 등급(access_level)을 붙인다.

나중에 붙이면 전체를 다시 색인해야 하므로(브리프 절대 규칙 4) 반입할 때 정한다. 값은 셋이다.
  public  전체 공개 — 공통 RAG. 별도로 정하지 않은 문서는 모두 여기(2026-10-13 결정: 기본은 공통, 부서 RAG 는 고를 때만)
  dept    부서 제한 — 부서 RAG 로 지정한 문서. 그 부서 계정(과 관리자·추가 권한 계정)만 본다
  owner   담당자 한정 — 올린 사람만 본다(민감한 접수 서류)
등급 값 체계는 학교와 확정 전이지만(미해결 질문 7번) 필드와 기본 규칙을 먼저 둔다.
근거: docs/notes/2026-09-22-access-controlled-knowledge-base.md.
"""

from __future__ import annotations

LEVELS = {"public": "전체 공개", "dept": "부서 제한", "owner": "담당자 한정"}
DEFAULT_DEPT = "공통"

# 문서 유형별 기본 등급 — 기준 문서(규정·공고)는 공개, 그 밖의 접수 문서는 부서 제한
_PUBLIC_TYPES = {"regulation", "criteria", "announcement"}


def default_level(doc_type: str | None, owner: str | None = None) -> str:
    """유형과 소유자로 정하는 기본 등급. 공개 수집분(owner=corpus)은 언제나 공개."""
    if owner == "corpus" or (doc_type or "") in _PUBLIC_TYPES:
        return "public"
    return "dept"


def classify(doc_type: str | None, *, owner: str | None = None, dept: str | None = None,
             access_level: str | None = None, uploader_dept: str | None = None,
             project_dept: str | None = None) -> tuple[str, str]:
    """반입 문서의 (부서, 등급). 기본은 공통 RAG — 올린 사람·프로젝트의 부서와 상관없이 「공통·전체 공개」로 들어간다.
    부서를 직접 고른 문서만 그 부서 RAG(부서 제한)에 든다 — 라이브러리 「문서 올리기」의 검색 범위, 문서 화면의 검색 범위,
    문서 반입의 폴더별 담당 부서. uploader_dept·project_dept 는 예전 호출과 맞추려고 받기만 한다.
    「담당자 한정(owner)」은 따로 고른 경우에만."""
    d = (dept or "").strip()
    if d and d != DEFAULT_DEPT:
        return d, access_level if access_level in LEVELS else "dept"
    return DEFAULT_DEPT, "owner" if access_level == "owner" else "public"


# 교직원 범위로 보는 역할 — 교직원(staff)·교수(professor)·부서장(head). 지금은 같은 범위(공통 + 자기 부서 + 추가 권한 부서 + 자기 문서),
# 같은 부서 안의 등급(부서장만 보는 문서 등)은 나중에 정한다(2026-10-15 사용자)
STAFF_ROLES = ("staff", "professor", "head")


def role_class(role: str | None) -> str:
    """화면·도우미가 쓰는 계정 갈래 — 교직원 범위 역할(교직원·교수·부서장)은 「staff」 하나로 본다."""
    return "staff" if role in STAFF_ROLES else (role or "")


def visible(doc: dict, *, dept: str | None, user: str | None, role: str, also: tuple | list = ()) -> bool:
    """이 문서를 이 사용자가 볼 수 있는가 — 열람 규칙의 원본. 목록 SQL(doc_sql)·검색 조건이 같은 뜻이다(tests/test_access_matrix).
    관리자=전부, 학생=학생 공개 규정만, 교직원=전체 공개 + 공통·자기 부서·추가 권한 부서의 부서 제한 문서 + 자기가 올린 문서.
    also: 계정별 추가 권한으로 더 받은 부서 RAG(rag_spaces grants) — 그 부서 문서도 연다."""
    if not doc:
        return False
    if role == "dev":
        return True
    level = doc.get("access_level") or "public"
    d = doc.get("dept") or DEFAULT_DEPT
    if role == "student":
        # 학생은 학생 공개로 지정된 규정만(학사 규정 RAG, ADR-0052) — 교직원 자료는 공개 등급이어도 보이지 않는다
        return level == "public" and doc.get("doc_type") == "regulation" and (doc.get("audience") or "staff") == "student"
    if level == "public" or (bool(user) and doc.get("owner") == user):
        return True
    if level == "dept":
        return d == DEFAULT_DEPT or (bool(dept) and d == dept) or d in (also or ())
    return False


def extra_depts(user: str | None, data_dir=None) -> list[str]:
    """계정별 추가 권한으로 받은 부서 RAG 이름들(RAG 공간 설정 grants). 학생·관리자에게는 주지 않는다(rag_spaces.grant_changes)."""
    if not user or data_dir is None:
        return []
    from zzaimy.app import rag_spaces
    return [s[5:] for s in rag_spaces.config(data_dir).get("grants", {}).get(user, []) if str(s).startswith("dept:")]


def viewer(role: str | None, dept: str | None, user: str | None, data_dir=None) -> dict:
    """열람 판정에 넘기는 보는 사람 — visible(doc, **viewer)·doc_sql(viewer) 가 같은 값을 쓴다. 계정의 모르는 역할은 교직원으로 보지 않고
    가장 좁게(학생과 같은 범위). visible 에 역할을 직접 넘기는 내부 호출(역할 빈 값)은 예전처럼 공개·공통만 본다."""
    role = role or ""
    if role not in ("dev", "student", *STAFF_ROLES):
        role = "student"
    also = tuple(extra_depts(user, data_dir)) if role in STAFF_ROLES else ()
    return {"role": role, "dept": (dept or "").strip() or None, "user": user or "", "also": also}


def request_viewer(request) -> dict:
    """요청의 보는 사람 — 로그인 검사(main.check_auth)가 request.state 에 둔 역할·부서·계정과 RAG 공간 설정의 추가 권한."""
    st = request.state
    data_dir = getattr(request.app.state, "data_dir", None)
    return viewer(getattr(st, "role", ""), getattr(st, "dept", "") or None, getattr(st, "user", ""), data_dir)


def can_browse(role: str | None) -> bool:
    """문서함·검색·지식그래프·RAG 공간 같은 문서 둘러보기 화면을 열 수 있는가 — 학생(과 모르는 역할)은 대화만."""
    return role in ("dev", *STAFF_ROLES)


def doc_sql(v: dict, alias: str = "d.") -> tuple[str, list]:
    """visible 과 같은 뜻의 SQL 조건(앞에 「 AND」 를 붙여 돌려준다) — 문서 목록·건수·칩·폴더·사업 목록이 이것으로 거른다.
    관리자는 조건 없음."""
    a = alias
    role = v.get("role") or ""
    if role == "dev":
        return "", []
    if role == "student":
        return (f" AND COALESCE({a}access_level, 'public') = 'public' AND {a}doc_type = 'regulation'"
                f" AND COALESCE({a}audience, 'staff') = 'student'"), []
    mine = [x for x in [v.get("dept") or "", *(v.get("also") or ())] if x]
    dept_in = f" OR COALESCE({a}dept, '{DEFAULT_DEPT}') IN ({','.join('?' * len(mine))})" if mine else ""
    user = v.get("user") or ""
    own = f" OR ({a}owner = ? AND {a}owner <> '')" if user else ""
    sql = (f" AND (COALESCE({a}access_level, 'public') = 'public'"
           f" OR ({a}access_level = 'dept' AND (COALESCE({a}dept, '{DEFAULT_DEPT}') = '{DEFAULT_DEPT}'{dept_in})){own})")
    return sql, [*mine, *([user] if user else [])]
