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


def visible(doc: dict, *, dept: str | None, user: str | None, role: str, also: tuple | list = ()) -> bool:
    """이 문서를 이 사용자가 볼 수 있는가 — 검색 SQL 과 같은 규칙(테스트·자가 점검에서 대조).
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
    if level == "public":
        return True
    if level == "dept":
        return d == DEFAULT_DEPT or (bool(dept) and d == dept) or d in (also or ())
    return bool(user) and doc.get("owner") == user
