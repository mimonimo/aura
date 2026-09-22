"""반입 시점의 접근 범위 규칙 — 문서마다 부서(dept)와 열람 등급(access_level)을 붙인다.

나중에 붙이면 전체를 다시 색인해야 하므로(브리프 절대 규칙 4) 반입할 때 정한다. 값은 셋이다.
  public  전체 공개 — 규정·공고·지침처럼 누구나 보는 기준 문서
  dept    부서 제한 — 접수 문서의 기본값. 올린 사람의 부서(또는 프로젝트의 부서)와 공통 계정만 본다
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
    """반입 문서의 (부서, 등급). 명시값이 있으면 그것, 없으면 올린 사람 → 프로젝트 → 공통 순."""
    level = access_level if access_level in LEVELS else default_level(doc_type, owner)
    chosen = (dept or "").strip() or (uploader_dept or "").strip() or (project_dept or "").strip() or DEFAULT_DEPT
    if level == "public" and not (dept or "").strip():
        chosen = DEFAULT_DEPT          # 공개 문서는 부서를 따로 정하지 않으면 공통
    return chosen, level


def visible(doc: dict, *, dept: str | None, user: str | None, role: str) -> bool:
    """이 문서를 이 사용자가 볼 수 있는가 — 검색 SQL 과 같은 규칙(테스트·자가 점검에서 대조)."""
    if role == "dev":
        return True
    level = doc.get("access_level") or "public"
    d = doc.get("dept") or DEFAULT_DEPT
    if role == "student":
        return level == "public" and d == DEFAULT_DEPT
    if level == "public":
        return True
    if level == "dept":
        return d == DEFAULT_DEPT or not dept or d == dept
    return bool(user) and doc.get("owner") == user
