"""수치 대조 검증기(브리프 5.3).

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
            n_num = Decimal(nm.group(1).replace(",", ""))
            if (
                gap.strip() == "" and n_unit
                and 0 < n_rank < last_unit_rank
            ):
                value += n_num * n_rank
                out.add(_fmt(n_num))
                last_unit_rank = n_rank
                j += 1
            elif gap.strip() == "" and not n_unit and n_num == n_num.to_integral_value() and 0 < n_num < last_unit_rank:
                # 단위 뒤의 맨숫자 — 「1만 1799명」 = 11,799, 「3억 2500만」의 끝자리처럼 남은 자리. 합산하지 않으면 같은 수를 근거 없음으로 띄운다
                value += n_num
                out.add(_fmt(n_num))
                j += 1
                break
            else:
                break
        if unit:
            out.add(_fmt(value))
        i = j if j > i + 1 else i + 1
    return out


# 같은 값의 단위 바꿈 — 「500(백만원)」 처럼 숫자 바로 뒤 괄호의 금액 단위, 표·문단의 「(단위: 백만원)」 선언.
# 그 단위로 환산한 값도 같은 값으로 본다(5억 = 500,000,000원 = 500백만원 = 500(백만원), 단위가 백만원인 표의 500).
_MONEY_UNIT = {"원": 1, "천원": 10**3, "만원": 10**4, "백만원": 10**6, "천만원": 10**7, "억원": 10**8}
_UNIT_ALT = "천만원|백만원|천원|만원|억원|원"
_DECLARED = re.compile(r"단위\s*[:：]?\s*(" + _UNIT_ALT + r")")
_PAREN_UNIT = re.compile(r"\s*[(（]\s*(?:단위\s*[:：]?\s*)?(" + _UNIT_ALT + r")\s*[)）]")


def declared_units(text: str) -> set[int]:
    """글에 선언된 금액 단위(「(단위: 백만원)」) — 배수 집합. 원 단위 선언(1)은 뺀다."""
    return {_MONEY_UNIT[u] for u in _DECLARED.findall(text or "") if _MONEY_UNIT[u] > 1}


def _decl_at(text: str, pos: int) -> int | None:
    """그 숫자에 걸리는 단위 선언 — 같은 덩이(빈 줄로 나뉜 문단·표) 안에서 그 숫자와 가장 가까운 선언 하나(표 위·아래 어느 쪽이든).
    보안: 글 어딘가의 선언을 모두 곱해 보면, 선언을 여러 개 넣는 것만으로 아무 숫자나 근거 값과 맞출 수 있다."""
    a = text.rfind("\n\n", 0, pos)
    start = 0 if a < 0 else a
    b = text.find("\n\n", pos)
    end = len(text) if b < 0 else b
    best, gap = None, None
    for d in _DECLARED.finditer(text, start, end):
        g = abs(d.start() - pos)
        if gap is None or g < gap:
            best, gap = _MONEY_UNIT[d.group(1)], g
    return best if best and best > 1 else None


_WON_AFTER = re.compile(r"\s*원")


def _money_value(text: str, m: re.Match) -> str | None:
    """금액으로 읽히는 숫자의 원 단위 값 — 뒤에 「원」, 바로 뒤 괄호 금액 단위, 또는 덩이 안 단위 선언이 있을 때만."""
    num = Decimal(m.group(1).replace(",", "")) * _UNIT_VALUE.get(m.group(2) or "", 1)
    if _WON_AFTER.match(text, m.end()):
        return _fmt(num)
    pm = _PAREN_UNIT.match(text, m.end())
    if pm:
        return _fmt(num * _MONEY_UNIT[pm.group(1)])
    if not m.group(2):
        d = _decl_at(text, m.start())
        if d:
            return _fmt(num * d)
    return None


def scaled_values(text: str) -> set[str]:
    """근거 글의 금액 값(원 단위) — 단위 바꿈 비교는 금액끼리만 한다."""
    cleaned = _LIST_MARKER.sub("\n", text or "")
    out: set[str] = set()
    for m in _NUM_UNIT.finditer(cleaned):
        v = _money_value(cleaned, m)
        if v:
            out.add(v)
    return out


def _allowed(evidence_texts: list[str]) -> tuple[set[str], set[str], set[str]]:
    surface: set[str] = set()
    canon: set[str] = set()
    money: set[str] = set()
    for ev in evidence_texts:
        surface |= extract_numbers(ev)
        canon |= canonical_values(ev)
        money |= scaled_values(ev)
    return surface, canon, money


def _supported(src: str, m: re.Match, surface_ok: set[str], canon_ok: set[str], money_ok: set[str]) -> bool:
    """근거에 있는 값인가 — 표면형·단위 환산(조·억·만…) 비교는 전처럼, 금액 단위 바꿈(500(백만원)=5억 원)은
    초안 쪽도 금액으로 읽힐 때만, 근거의 금액 값과만 견준다(사람 수·건수 같은 값과 단위 바꿈으로 맞추지 않게)."""
    s = m.group(1).replace(",", "")
    unit = m.group(2)
    if s in surface_ok or s in canon_ok or _fmt(Decimal(s) * _UNIT_VALUE.get(unit or "", 1)) in canon_ok:
        return True
    v = _money_value(src, m)
    return bool(v) and v in money_ok


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
    allowed_surface, allowed_canonical, allowed_money = _allowed(evidence_texts)
    violations: list[str] = []
    contexts: list[str] = []
    cleaned = _LIST_MARKER.sub("\n", draft)
    for m in _NUM_UNIT.finditer(cleaned):
        surface = m.group(1).replace(",", "")
        if _supported(cleaned, m, allowed_surface, allowed_canonical, allowed_money):
            continue
        if surface not in violations:
            violations.append(surface)
            contexts.append(_context_of(draft, m.group(1)))
    return NumberAudit(
        ok=not violations, violations=sorted(violations), contexts=contexts
    )


_YEAR_LIKE = re.compile(r"^(?:19|20)\d{2}$")


def mask_unsupported(text: str, evidence_texts: list[str]) -> tuple[str, list[str]]:
    """근거에 없는 수치를 「○○」로 가린다(단위는 남긴다: 300명 → ○○명) — 절대 규칙 1(수치는 인출만).
    연도(2026)·목록 번호·10 이하의 구조 숫자(「2개년」「3대 전략」, % 가 붙으면 수치)는 그대로. 돌려주는 것은 (새 글, 가린 원래 수치)."""
    return _rewrite_unsupported(text, evidence_texts, mark=False)


FLAG = "(근거 확인 필요)"


def flag_unsupported(text: str, evidence_texts: list[str]) -> tuple[str, list[str]]:
    """근거에 없는 수치 바로 뒤에 「(근거 확인 필요)」를 붙인다 — 대화 답은 수치를 지우지 않고, 어느 수치를 원문에서 확인해야 하는지
    보이게 한다(문서 초안은 mask_unsupported 로 가린다). 판정은 mask_unsupported 와 같다. 돌려주는 것은 (새 글, 표시한 수치)."""
    return _rewrite_unsupported(text, evidence_texts, mark=True)


def _rewrite_unsupported(text: str, evidence_texts: list[str], mark: bool) -> tuple[str, list[str]]:
    allowed_surface, allowed_canonical, allowed_money = _allowed(evidence_texts)
    out, masked, last = [], [], 0
    src = text or ""
    for m in _NUM_UNIT.finditer(src):
        surface = m.group(1).replace(",", "")
        unit = m.group(2)
        line_start = src.rfind("\n", 0, m.start()) + 1
        after = src[m.end():m.end() + 2]
        if src[line_start:m.start()].strip() == "" and re.match(r"[.)]\s", after):
            continue                                          # 목록 번호 「1. 」「2) 」
        if _YEAR_LIKE.match(surface):
            continue
        before, after1 = src[max(0, m.start() - 1):m.start()], src[m.end(1):m.end(1) + 2]
        if (before in ("-", "/", ":", "~", ".") and m.start() >= 2 and src[m.start() - 2].isdigit()) or re.match(r"[-/:]\d", after1) \
                or re.match(r"\.\d", after1):
            continue                                          # 전화번호·날짜·코드(010-1234-5678, 2026.4.3, 3/4)는 수치가 아니다
        tail = src[m.end():m.end() + 1]
        try:
            small = Decimal(surface) <= 10 and not unit and tail != "%"
        except Exception:
            small = False
        if small:
            continue
        if _supported(src, m, allowed_surface, allowed_canonical, allowed_money):
            continue
        if mark:
            end = m.end()
            while end < len(src) and src[end] in "%개명원건회배년월일점":
                end += 1                                   # 단위까지 붙여 「300명(근거 확인 필요)」
            out.append(src[last:end] + FLAG)
            last = end
        else:
            out.append(src[last:m.start(1)] + "○○")
            last = m.end(1)
        masked.append(m.group(0).strip())
    out.append(src[last:])
    return "".join(out), masked
