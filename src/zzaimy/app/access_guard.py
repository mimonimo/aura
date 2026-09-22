"""권한 밖 질문 대처 — 검색 범위 밖의 자료는 모델에게 건네지지 않는다는 원칙 위에 얹는 네 겹.

근거는 docs/notes/2026-09-22-access-controlled-knowledge-base.md.
  1. 개인정보 요청은 권한과 무관하게 답하지 않는다 — 결정론 판정(개인정보 유형 낱말 + 특정인 지칭).
  2. 범위 밖 자료를 가리키는 질문은 거절이 아니라 안내로 답한다(다른 부서명이 질문에 있을 때).
  3. 유도 질문은 근거 규칙으로 버틴다 — 여기서는 판정만 하고, 모델 지시는 responder 가 맡는다.
  4. 시도를 기록하고 반복되면 관리자 화면에 보인다(JSONL 감사 기록).
답변이 나간 뒤에는 마스킹 검사기가 응답 본문을 한 번 더 훑는다(scrub).
판단은 모델의 재량이 아니라 규칙과 검색이 한다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# 역할 — 계정에 붙는 값. staff·head 는 부서 범위, student 는 공통 공개 자료만, dev 는 관리 화면.
ROLES = {"staff": "담당자", "head": "부서장", "student": "학생", "dev": "관리자"}

# 개인정보 유형 낱말 — 이 낱말과 '특정인 지칭'이 함께 있으면 개인정보 요청으로 본다.
_PII_WORDS = (
    "주민등록번호", "주민번호", "전화번호", "휴대폰", "휴대전화", "핸드폰", "연락처", "계좌", "계좌번호",
    "연봉", "급여", "월급", "생년월일", "집 주소", "자택", "이메일 주소", "메일 주소", "여권번호", "운전면허",
)
# 특정인 지칭 — 성명(2~4자 한글)+호칭, 또는 '○○ 교수/직원/학생/담당자'
_PERSON = re.compile(
    r"[가-힣]{2,4}\s*(?:씨|님|교수|교수님|선생님|직원|학생|담당자|팀장|과장|부장|처장|단장|대표|원장)"
    r"|(?:그|해당|담당)\s*(?:직원|학생|교수|담당자)의?"
)
# 유도 질문의 흔한 꼴 — 근거가 아니므로 표시만 한다
_INJECTION = re.compile(
    r"이전\s*지시|지시를?\s*무시|규칙을?\s*무시|시스템\s*프롬프트|관리자(?:다|입니다|권한)|전부\s*(?:보여|출력|알려)"
    r"|숨기지\s*말고|필터\s*(?:없이|끄고)|제한\s*없이"
)

# 교직원 업무 자료를 가리키는 말 — 학생 계정의 범위 밖
_STAFF_MATERIAL = re.compile(
    r"사업계획서|결과보고서|접수\s*(?:문서|서류)|채용\s*서류|지원자|응시자|입학\s*전형\s*자료|평가\s*(?:표|자료)"
    r"|내부\s*(?:문서|자료)|원문\s*(?:보여|출력|전부)|검토\s*의견|초안"
)

PII_NOTE = (
    "개인정보(연락처·주민번호·계좌·급여 등)는 이 시스템이 보관하지 않으며 답변에 쓰지 않습니다. "
    "필요하면 해당 자료를 관리하는 부서에 직접 확인해 주십시오."
)


def _dept_words(depts: list[str]) -> list[str]:
    """부서 이름에서 '공통' 같은 범용 값을 뺀 목록 — 질문에 다른 부서명이 있는지 볼 때 쓴다."""
    return [d for d in depts if d and d not in ("공통", "common", "전체")]


def pii_request(question: str) -> str | None:
    """특정인의 개인정보를 묻는 질문이면 안내문, 아니면 None. 권한과 무관하게 적용한다."""
    q = question or ""
    if any(w in q for w in _PII_WORDS) and _PERSON.search(q):
        return PII_NOTE
    return None


def injection_like(question: str) -> bool:
    """유도 질문의 꼴인가 — 답을 막지는 않는다. 근거 규칙이 버티고, 기록에 남긴다."""
    return bool(_INJECTION.search(question or ""))


def scope_note(question: str, dept: str | None, role: str, depts: list[str]) -> str | None:
    """질문이 사용자 범위 밖의 부서 자료를 가리키면 안내문, 아니면 None.

    학생은 부서 자료 전체가 범위 밖이다. 담당자·부서장은 자기 부서와 공통만 범위다.
    존재 여부 자체가 비밀인 자료는 없다고 전제한다(있다면 별도 저장소에 두고 검색 대상에서 뺀다).
    """
    q = question or ""
    mentioned = [d for d in _dept_words(depts) if d in q]
    if role == "student":
        # 부서명이 자료에 없어도 업무 자료를 가리키는 말이면 안내한다(사업계획서·접수 서류·지원자…)
        if mentioned or _STAFF_MATERIAL.search(q):
            return ("학생 계정에서는 부서 업무 자료를 볼 수 없습니다. 공개된 규정과 학사 안내만 답할 수 있으며, "
                    "그 밖의 문의는 학생 민원 창구나 해당 부서에 해 주십시오.")
        return None
    if role == "dev" or not dept:
        return None
    others = [d for d in mentioned if d != dept]
    if others:
        return (f"{', '.join(others)} 자료는 현재 계정({dept})의 열람 범위에 없습니다. "
                "공통 규정과 소속 부서 자료로만 답합니다. 필요하면 해당 부서나 관리자에게 요청해 주십시오.")
    return None


def search_scope(dept: str | None, role: str, user: str | None = None) -> dict:
    """검색에 넘길 범위 — 학생은 공통의 공개 자료만, 담당자·부서장은 자기 부서 + 공통(등급 규칙 적용),
    관리자는 전체. 부서가 없는 담당자도 등급 규칙(담당자 한정은 본인만)은 받는다."""
    if role == "student":
        return {"dept": "공통", "levels": ("public",)}
    if role == "dev":
        return {}
    scope: dict = {"user": user or ""}
    if dept:
        scope["dept"] = dept
    return scope


def allowed_doc_ids(db, doc_ids: list[int], dept: str | None, role: str, user: str | None = None) -> list[int]:
    """담당자가 직접 고른 기준 문서도 범위 안의 것만 남긴다 — 검색 SQL 과 같은 규칙(access_policy.visible)."""
    from zzaimy.app.access_policy import visible

    if not doc_ids or role == "dev":
        return list(doc_ids)
    out: list[int] = []
    for did in doc_ids:
        d = db.get_document(did) or {}
        if visible(d, dept=dept, user=user, role=role):
            out.append(did)
    return out


# ---------------------------------------------------------------- 감사 기록

def _audit_path(data_dir: Path) -> Path:
    return Path(data_dir) / "access_audit.jsonl"


def audit(data_dir: Path, user: str, kind: str, question: str, dept: str | None = None, role: str = "") -> None:
    """권한 밖 시도·개인정보 요청·유도 질문을 한 줄로 남긴다. 질문은 앞 120자만."""
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "user": user, "kind": kind,
           "dept": dept or "", "role": role, "question": (question or "")[:120]}
    p = _audit_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def recent(data_dir: Path, hours: int = 24, limit: int = 100) -> list[dict]:
    """최근 시도 목록(최신 먼저) — 관리자 화면용."""
    p = _audit_path(data_dir)
    if not p.exists():
        return []
    since = datetime.now() - timedelta(hours=hours)
    out: list[dict] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
            if datetime.fromisoformat(rec["at"]) >= since:
                out.append(rec)
        except (ValueError, KeyError):
            continue
    out.reverse()
    return out[:limit]


KIND_LABELS = {"pii": "개인정보 요청", "scope": "범위 밖 자료", "injection": "유도 질문"}


# ---------------------------------------------------------------- 답변 후 검사

_masker = None


def scrub(text: str) -> str:
    """답변 본문에 남은 식별 정보를 가린다 — 접수 문서의 마스킹과 같은 검사기."""
    global _masker
    if not text:
        return text
    try:
        if _masker is None:
            from zzaimy.ingest.pii import PiiMasker

            _masker = PiiMasker()
        from zzaimy.ingest.pii import RawDocument

        masked, _ = _masker.mask(RawDocument(doc_id="answer", text=text))
        return masked.text
    except Exception:
        return text
