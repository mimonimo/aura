"""기관 정보 — 대학명·주소 같은 값은 규정·문서함에서 인출하고, 총장 성명처럼 문서에 없는 값은 설정(institution:<이름>)으로 둔다.

교내 사업 문서에는 대학명·총장 이름이 들어가야 하므로 규정·문서에서 찾고, 없는 값은 비워 두고 알린다.
설정값이 있으면 그것이 우선이다(담당자가 바꿀 수 있어야 한다).
"""

from __future__ import annotations

import re
from collections import Counter

KEYS = ("대학명", "주소", "총장", "대표전화", "우편번호", "홈페이지", "전자우편", "팩스")
BASE_KEYS = KEYS[:4]                   # 처음 묻는 기관 정보 — 결문 연락처(우편번호·홈페이지·전자우편·팩스)는 담당자가 알려 줄 때만 쌓인다
# 공문 결문의 연락처 칸 → 기관 정보 이름. 이 칸들은 기관 정보에서만 채운다 — 문서함의 다른 기관 문서에서 옮기면 남의 학교 연락처가
# 들어간다(11-09 시연: 홈페이지·전자우편이 다른 대학 것으로 채워짐)
CONTACT = {"우편번호": "우편번호", "주소": "주소", "홈페이지": "홈페이지", "전자우편": "전자우편", "이메일": "전자우편",
           "전화": "대표전화", "대표전화": "대표전화", "전송": "팩스", "전송(팩스)": "팩스", "팩스": "팩스"}
_UNI = re.compile(r"([가-힣]{2,12}대학교)(?![가-힣])")
_GENERIC = ("참여대학교", "주관대학교", "협력대학교", "해당대학교", "각대학교", "전문대학교", "일반대학교")
_ADDR = re.compile(r"((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청?북|충청?남|전라?북|전라?남|경상?북|경상?남|제주)[가-힣]*(?:특별시|광역시|특별자치시|특별자치도|도)?\s*[가-힣]+(?:시|군|구)\s*[가-힣0-9]+(?:로|길|동)\s*\d+[-\d]*)")


def _texts(db, limit: int = 3000) -> list[str]:
    out: list[str] = []
    with db._conn() as conn:
        for table in ("regulation_chunks", "doc_chunks"):
            try:
                rows = conn.execute(f"SELECT content FROM {table} WHERE content LIKE '%대학교%' LIMIT ?", (limit,)).fetchall()
            except Exception:
                continue
            out += [r[0] or "" for r in rows]
    return out


def facts(db) -> dict:
    """{대학명, 주소, 총장, 대표전화} — 설정값 우선, 없으면 문서에서 인출, 그래도 없으면 빈 문자열."""
    out = {k: (db.get_setting(f"institution:{k}", "") or "").strip() for k in KEYS}
    if not (out["대학명"] and out["주소"]):
        texts = _texts(db)
        if not out["대학명"]:
            names = Counter(m for t in texts for m in _UNI.findall(t) if m not in _GENERIC)
            out["대학명"] = names.most_common(1)[0][0] if names else ""
        if not out["주소"] and out["대학명"]:
            addrs = Counter(m for t in texts if out["대학명"] in t for m in _ADDR.findall(t))
            out["주소"] = addrs.most_common(1)[0][0] if addrs else ""
    return out


def contact_key(field: str) -> str | None:
    """칸 이름(「칸:전송(팩스)」 꼴도) → 기관 정보 이름. 결문 연락처 칸이 아니면 None."""
    label = re.sub(r"\s+", "", str(field or "").split(":", 1)[-1])
    return CONTACT.get(label)


def has_contact_block(names) -> bool:
    """공문 결문(우편번호와 홈페이지·전자우편이 함께 있는 연락처 묶음)이 있는 문서인가 — 다른 서식의 「주소」「전화」 칸은 건드리지 않게."""
    keys = {contact_key(n) for n in names}
    return "우편번호" in keys and bool(keys & {"홈페이지", "전자우편"})


def set_fact(db, key: str, value: str) -> None:
    if key in KEYS:
        db.set_setting(f"institution:{key}", (value or "").strip())


_ANSWER = {
    "총장": re.compile(r"총장(?:님)?(?:\s*(?:은|는|:|：|이름은|성명은))?\s*([가-힣]{2,4})(?=\s|[,.;·]|$|입니다|이다|이고|이며)"),
    "대표전화": re.compile(r"(?:대표\s*전화|대표번호|전화번호|전화)(?:\s*(?:는|은|:|：))?\s*(0\d{1,2}[-. )]?\d{3,4}[-. ]?\d{4})"),
    "주소": re.compile(r"주소(?:\s*(?:는|은|:|：))?\s*((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청?[북남]|전라?[북남]|경상?[북남]|제주)[^,;\n]{6,60})"),
    "대학명": re.compile(r"대학명(?:\s*(?:은|는|:|：))?\s*([가-힣]{2,12}대학교?)"),
}


def form_option(facts: dict) -> dict:
    """대화 선택지에 실어 보내는 입력 양식 — 화면은 fields 를 입력 칸으로 그리고, 채운 값을 template 에 넣은 문장을 질문으로 보낸다
    (kind=form 규격). 양식을 못 그리는 화면은 question 을 버튼으로 쓴다."""
    missing = [k for k in BASE_KEYS if not (facts.get(k) or "").strip()]
    hints = {"총장": "예: 홍길동", "대표전화": "예: 053-000-0000", "주소": "예: 대구광역시 남구 현충로 170", "대학명": "예: 영남이공대학교"}
    fields = [{"name": k, "label": k + (" 성명" if k == "총장" else ""), "placeholder": hints.get(k, ""), "required": False} for k in missing]
    template = ", ".join(f"{k}은 {{{k}}}" if k in ("총장", "대학명") else f"{k}는 {{{k}}}" for k in missing)
    return {"kind": "form", "text": "기관 정보 입력", "fields": fields, "template": template,
            "question": ", ".join(f"{k}은 ○○○" if k in ("총장", "대학명") else f"{k}는 (값)" for k in missing),
            "submit": "저장"}
