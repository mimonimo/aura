"""기관 정보 — 대학명·주소 같은 값은 규정·문서함에서 인출하고, 총장 성명처럼 문서에 없는 값은 설정(institution:<이름>)으로 둔다.

사용자 지시 2026-09-27: 우리 대학 사업이니 대학명·총장 이름이 들어가야 한다. 규정을 RAG 로 해서. 없는 값은 비워 두고 알린다.
설정값이 있으면 그것이 우선(담당자가 바꿀 수 있어야 한다).
"""

from __future__ import annotations

import re
from collections import Counter

KEYS = ("대학명", "주소", "총장", "대표전화")
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


def set_fact(db, key: str, value: str) -> None:
    if key in KEYS:
        db.set_setting(f"institution:{key}", (value or "").strip())


_ANSWER = {
    "총장": re.compile(r"총장(?:님)?(?:\s*(?:은|는|:|：|이름은|성명은))?\s*([가-힣]{2,4})(?=\s|[,.;·]|$|입니다|이다|이고|이며)"),
    "대표전화": re.compile(r"(?:대표\s*전화|대표번호|전화번호|전화)(?:\s*(?:는|은|:|：))?\s*(0\d{1,2}[-. )]?\d{3,4}[-. ]?\d{4})"),
    "주소": re.compile(r"주소(?:\s*(?:는|은|:|：))?\s*((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청?[북남]|전라?[북남]|경상?[북남]|제주)[^,;\n]{6,60})"),
    "대학명": re.compile(r"대학명(?:\s*(?:은|는|:|：))?\s*([가-힣]{2,12}대학교?)"),
}


def parse_answer(text: str) -> dict:
    """담당자가 대화로 알려 준 기관 정보 — '총장은 홍길동, 대표전화는 053-650-9000' 같은 말에서 값을 꺼낸다."""
    out: dict = {}
    for k, rx in _ANSWER.items():
        m = rx.search(text or "")
        if m:
            out[k] = m.group(1).strip()
    return out


def ask_for_missing(facts: dict) -> str:
    """비어 있는 값을 묻는 한 줄 — 에이전트가 모르는 값을 지어내지 않고 요청한다(사용자 지시 2026-09-28)."""
    missing = [k for k in KEYS if not (facts.get(k) or "").strip()]
    if not missing:
        return ""
    return ("기관 정보 중 " + "·".join(missing) + " 을(를) 문서함에서 찾지 못했습니다. 알려 주시면 기억해 두고 이후 작성에 넣겠습니다"
            " — 예: \"총장은 홍길동, 대표전화는 (번호)\".")
