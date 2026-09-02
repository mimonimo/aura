"""검증기 1종 — 수치 대조 (W1-W2 TASK-08, 브리프 5.3).

생성 초안의 모든 수치는 인출된 근거에 존재해야 한다. 결정론적으로 판정하며
LLM 판단을 쓰지 않는다. 이 모듈은 Writer 학습 데이터 정제기와 공유된다
(model-plan §2 — 학습과 서빙이 같은 기준으로 수치를 판정).

한국어 행정문서는 같은 값을 "60,000,000원 / 6천만 원 / 60백만 원"처럼
여러 표기로 쓴다. 표면형 비교만 하면 가짜 위반이 폭주하므로, 표면형과
함께 단위를 환산한 정준값(canonical value)으로도 대조한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

# 천단위 콤마 허용, 소수 허용. 예: 1,234 / 4.2 / 120
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# 목록 마커("1. ", "2) ")는 구조 숫자 — 수치가 아니다
_LIST_MARKER = re.compile(r"(?:^|\n)\s*\d+[.)]\s")

# 숫자 + 한국어 단위 토큰. "3억", "5천만", "60백만", "2,500만" 등
_UNIT_VALUE = {
    "조": 10**12, "억": 10**8,
    "천만": 10**7, "백만": 10**6, "십만": 10**5, "만": 10**4,
    "천": 10**3, "백": 10**2, "십": 10,
}
_NUM_UNIT = re.compile(
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(조|억|천만|백만|십만|만|천|백|십)?"
)


def extract_numbers(text: str) -> set[str]:
    """텍스트에서 수치를 정규형(콤마 제거)으로 추출한다."""
    cleaned = _LIST_MARKER.sub("\n", text)
    return {m.group().replace(",", "") for m in _NUMBER.finditer(cleaned)}


def _fmt(value: Decimal) -> str:
    """정준값 문자열 — 정수는 정수로, 소수는 뒤 0 제거."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def canonical_values(text: str) -> set[str]:
    """단위를 환산한 정준값 집합. '3억 5천만'처럼 이어지는 단위는 합산한다."""
    cleaned = _LIST_MARKER.sub("\n", text)
    out: set[str] = set()
    matches = list(_NUM_UNIT.finditer(cleaned))
    i = 0
    while i < len(matches):
        m = matches[i]
        num = Decimal(m.group(1).replace(",", ""))
        unit = m.group(2)
        value = num * _UNIT_VALUE.get(unit or "", 1)
        out.add(_fmt(num))  # 표면 숫자 자체도 정준 집합에 (콤마 제거형)
        last_unit_rank = _UNIT_VALUE.get(unit or "", 0)
        j = i + 1
        # 바로 뒤에 더 작은 단위가 이어지면 합성 값 ("3억 5천만" = 3.5e8)
        while unit and j < len(matches):
            nm = matches[j]
            gap = cleaned[matches[j - 1].end():nm.start()]
            n_unit = nm.group(2)
            n_rank = _UNIT_VALUE.get(n_unit or "", 0)
            if (
                gap.strip() == "" and n_unit
                and 0 < n_rank < last_unit_rank
            ):
                value += Decimal(nm.group(1).replace(",", "")) * n_rank
                out.add(_fmt(Decimal(nm.group(1).replace(",", ""))))
                last_unit_rank = n_rank
                j += 1
            else:
                break
        if unit:
            out.add(_fmt(value))
        i = j if j > i + 1 else i + 1
    return out


@dataclass(frozen=True)
class NumberAudit:
    ok: bool
    violations: list[str]  # 근거 없는 수치들 (정규형)
    contexts: list[str] = field(default_factory=list)  # 위반별 초안 속 맥락


def _context_of(text: str, token: str, radius: int = 28) -> str:
    idx = text.find(token)
    if idx < 0:
        # 콤마 표기로 들어 있을 수 있다
        with_comma = re.sub(r"(\d)(?=(\d{3})+$)", r"\1,", token) if token.isdigit() else token
        idx = text.find(with_comma)
        token = with_comma if idx >= 0 else token
    if idx < 0:
        return token
    lo, hi = max(0, idx - radius), min(len(text), idx + len(token) + radius)
    snippet = text[lo:hi].replace("\n", " ")
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(text) else "")


def verify_numbers(draft: str, evidence_texts: list[str]) -> NumberAudit:
    """초안의 수치가 전부 근거 텍스트에 존재하는지 대조한다.

    표면형(콤마 제거) 또는 정준값(단위 환산) 어느 쪽으로든 근거에 있으면 통과.
    """
    allowed_surface: set[str] = set()
    allowed_canonical: set[str] = set()
    for ev in evidence_texts:
        allowed_surface |= extract_numbers(ev)
        allowed_canonical |= canonical_values(ev)

    violations: list[str] = []
    contexts: list[str] = []
    cleaned = _LIST_MARKER.sub("\n", draft)
    for m in _NUM_UNIT.finditer(cleaned):
        surface = m.group(1).replace(",", "")
        unit = m.group(2)
        value = _fmt(Decimal(surface) * _UNIT_VALUE.get(unit or "", 1))
        if (
            surface in allowed_surface
            or value in allowed_canonical
            or surface in allowed_canonical
        ):
            continue
        if surface not in violations:
            violations.append(surface)
            contexts.append(_context_of(draft, m.group(1)))
    return NumberAudit(
        ok=not violations, violations=sorted(violations), contexts=contexts
    )
