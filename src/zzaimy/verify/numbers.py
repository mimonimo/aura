"""수치 대조 검증기(브리프 5.3).

생성 초안의 모든 수치는 인출된 근거에 존재해야 한다. 결정론적으로 판정하며
LLM 판단을 쓰지 않는다. 이 모듈은 Writer 학습 데이터 정제기와 공유된다
(model-plan §2 — 학습과 서빙이 같은 기준으로 수치를 판정).

한국어 행정문서는 같은 값을 "60,000,000원 / 6천만 원 / 60백만 원"처럼
여러 표기로 쓴다. 표면형 비교만 하면 가짜 위반이 폭주하므로, 표면형과
함께 단위를 환산한 정준값(canonical value)으로도 대조한다.
"""

from __future__ import annotations

import os
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
_UNIT_ALT = r"천만\s?원|백만\s?원|천\s?원|만\s?원|억\s?원|원"     # 「백만 원」 처럼 띄어 쓴 단위도(10-30 「(단위 : 백만 원, %)」 표)
_DECLARED = re.compile(r"단위\s*[:：]?\s*(" + _UNIT_ALT + r")")
_PAREN_UNIT = re.compile(r"\s*[(（]\s*(?:단위\s*[:：]?\s*)?(" + _UNIT_ALT + r")\s*[)）]")


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
            best, gap = _MONEY_UNIT[re.sub(r"\s", "", d.group(1))], g
    return best if best and best > 1 else None


def _col_unit(text: str, pos: int, bare: bool = False) -> int | None:
    """표 칸 숫자의 열 머리 단위 — 같은 표(이어진 「|」 줄)의 첫 줄에서 같은 열 머리 칸이 「금액(천원)」 처럼 금액 단위를 품으면 그 배수.
    선언과 달리 그 열 하나에만 걸린다(다른 열·다른 표의 숫자에 번지지 않게)."""
    ls = text.rfind("\n", 0, pos) + 1
    le = text.find("\n", pos)
    line = text[ls:len(text) if le < 0 else le]
    lead = line.lstrip().startswith("|")
    # 앞 「|」 없이 칸을 「 | 」 로 이은 줄(엑셀·한글 표를 읽은 근거 글 「2026.04.07 | 홍보비 | 450000」)도 표 줄이다 — bare 는 근거 쪽만
    if not lead and not (bare and line.count(" | ") >= 2):
        return None

    def _row(t: str) -> bool:
        return t.lstrip().startswith("|") if lead else t.count(" | ") >= 2
    col = text[ls:pos].count("|") - (1 if lead else 0)
    head = ls
    while head > 0:                                       # 표의 첫 줄(머리줄)까지 거슬러
        prev = text.rfind("\n", 0, head - 1) + 1
        if not _row(text[prev:head - 1]):
            break
        head = prev
    if head == ls:
        return None                                       # 이 줄이 머리줄이면 단위를 매기지 않는다
    he = text.find("\n", head)
    hl = text[head:len(text) if he < 0 else he].strip()
    cells = (hl.strip("|") if lead else hl).split("|")
    if col < 0 or col >= len(cells):
        return None
    m = re.search(r"[(（]\s*(?:단위\s*[:：]?\s*)?(" + _UNIT_ALT + r")\s*[)）]|단위\s*[:：]?\s*(" + _UNIT_ALT + r")", cells[col])
    if not m:
        return None
    # 「금액(원)」 열도 금액이다(배수 1) — 집행 내역 엑셀의 「지출액(원)」 열 값이 금액으로 읽히지 않아 천원 표의 값과 맞추지 못했다(11-23 묶음 B)
    return _MONEY_UNIT[re.sub(r"\s", "", m.group(1) or m.group(2))]


_WON_AFTER = re.compile(r"\s*원")
_COUNTER_AFTER = re.compile(r"\s*(명|건|개|회|곳|%|퍼센트|년|월|일|시간|학점|과목|팀|기관|대|점|차|기|위|배|쪽|㎡|m)")


def _money_value(text: str, m: re.Match, draft: bool = False) -> str | None:
    """금액으로 읽히는 숫자의 원 단위 값 — 뒤에 「원」, 바로 뒤 괄호 금액 단위, 또는 덩이 안 단위 선언이 있을 때만."""
    num = Decimal(m.group(1).replace(",", "")) * _UNIT_VALUE.get(m.group(2) or "", 1)
    if _WON_AFTER.match(text, m.end()):
        return _fmt(num)
    pm = _PAREN_UNIT.match(text, m.end())
    if pm:
        return _fmt(num * _MONEY_UNIT[re.sub(r"\s", "", pm.group(1))])
    # 단위 선언으로 금액이 되는 것은 표 칸의 숫자만, 뒤에 셈 단위(명·건·%·개…)가 붙지 않은 것만 — 문장 속 아무 숫자나
    # 선언 하나로 금액이 되어 근거의 금액과 맞춰지지 않게(보안)
    if not m.group(2) and not _COUNTER_AFTER.match(text, m.end()):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:m.start()].lstrip().startswith("|") or not draft:
            cu = _col_unit(text, m.start(), bare=not draft)     # 열 머리 「금액(천원)」 — 11-17 예산 표 10배 오기를 놓치던 것
            if cu:
                return _fmt(num * cu)
        if not draft or text[line_start:m.start()].lstrip().startswith("|"):   # 근거 쪽은 믿는 글이라 문단 선언도
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
    # 코드가 더한 집계 줄(「◇ 집계 홍보비 · 지출액: a+b=9,800,000」)의 큰 합계는 금액으로도 본다 — 기록 값에 「원」 이 없으면(엑셀 열 값)
    # 합계도 「원」 없이 남아, 천원 단위 표의 「9,800」 을 근거와 맞추지 못했다(11-23 묶음 B). 만 이상만(사람 수·건수와 섞이지 않게)
    for ln in cleaned.splitlines():
        if ln.startswith("◇ 집계"):
            for m in re.finditer(r"[=:]\s*(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d.,/])", ln):
                v = Decimal(m.group(1).replace(",", ""))
                if v >= 10000:
                    out.add(_fmt(v))
    # 이어 적은 금액(「1억 2천만 원」「3억 3,750만 원」)은 덩이를 합한 값도 — 끝 덩이만 「원」 을 달고 있어 앞 덩이(1억)를 잃었다(11-17)
    for m in re.finditer(r"(?:" + _BIG + r"\s?)+(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?\s?원", cleaned):
        v = _amount_value(m.group(0).replace("원", ""))
        if v is not None and v == int(v):
            out.add(_fmt(Decimal(int(v))))
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
    v = _money_value(src, m, draft=True)
    # 열 머리가 「집행액(천원)」 인 칸에 근거의 원 단위 금액(9,800,000)을 그대로 넣으면 천 배다 — 표면 글자는 근거에 있어도 받지 않는다.
    # 11-23 3차 녹화 01: 세목별 집행액(천원) 표에 9,800,000·22,700,000 이 들어감. 근거가 이 수를 원 금액으로 가질 때만 본다(좁게)
    if (not unit and v and v not in money_ok and _fmt(Decimal(s)) in money_ok and Decimal(v) > Decimal(s)
            and src[src.rfind("\n", 0, m.start()) + 1:m.start()].lstrip().startswith("|")):
        return False
    if s in surface_ok or s in canon_ok or _fmt(Decimal(s) * _UNIT_VALUE.get(unit or "", 1)) in canon_ok:
        return True
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


_YEAR_LIKE = re.compile(r"^(?:19|20)\d{2}(?:\.(?:0?[1-9]|1[0-2]))?$")   # 연도와 「연도.월」(2023.3·2024.12) — 날짜 표기는 수치가 아니다


def mask_unsupported(text: str, evidence_texts: list[str], context: bool = False) -> tuple[str, list[str]]:
    """근거에 없는 수치를 「○○」로 가린다(단위는 남긴다: 300명 → ○○명) — 절대 규칙 1(수치는 인출만).
    연도(2026)·목록 번호·10 이하의 구조 숫자(「2개년」「3대 전략」, % 가 붙으면 수치)는 그대로. 돌려주는 것은 (새 글, 가린 원래 수치).
    context=True 면 수치 맥락 대조(아래 「수치 맥락」)를 더한다 — 같은 숫자가 근거 어딘가에 있는 것만으로는 통과하지 않는다."""
    return _rewrite_unsupported(text, evidence_texts, mark=False, context=context)


# ---------------------------------------------------------------- 수치 맥락
# 근거 글 어딘가에 같은 숫자만 있으면 통과하던 것 — 「2026. 2. 1.」 의 2 로 「대상 2학년」, 「총 4회」 를 지어 넣어도 막지 못했다(11-19).
# 맥락 대조: 숫자 바로 뒤 셈 단위(명·회·주·학년…, 글에서 그대로 떼어 낸다)가 근거의 같은 숫자와 맞아야 하고, 10 이하의 셈 숫자는
# 근거 쪽 둘레 낱말(앞뒤 몇 글자의 내용 낱말)도 하나 이상 겹쳐야 한다. 담당자가 직접 준 글(Given — 요청·대화)과 코드가 더한 집계 줄
# (「◇ 집계」)은 숫자+단위만 맞으면 근거로 본다. 금액(원·억·만…, 단위 환산)·%·날짜·셈 결과는 예전 판정 그대로.


class Given(str):
    """담당자가 직접 준 글(이번 요청·대화에 쓴 글) — 수치 맥락 대조에서 숫자+단위만 맞으면 근거로 본다."""


def given(text) -> "Given":
    return Given(str(text or ""))


def context_check_on() -> bool:
    """수치 맥락 대조 설정 — ZZAIMY_NUMBER_CONTEXT=0 이면 예전처럼 숫자만 대조한다."""
    return (os.environ.get("ZZAIMY_NUMBER_CONTEXT", "1") or "1").strip().lower() not in ("0", "off", "false", "no")


# 단위 뒤에 붙는 조사·어미(문법 낱말) — 「4명이」「2학년을」「4회의」 의 단위는 명·학년·회
_PARTICLES = ("으로", "에서", "에게", "부터", "까지", "이며", "이고", "이다", "이나", "마다",
              "이", "가", "을", "를", "은", "는", "의", "에", "와", "과", "로", "도", "만", "씩", "째")
# 10 이하일 때 사실 값이 아니라 글의 짜임·날짜를 세는 단위 — 예전처럼 대조하지 않는다(「3대 전략」「2단계」「2개 분야」「3월」)
_STRUCTURAL = {"대", "단계", "차", "차년도", "년차", "개년", "개년도", "가지", "번째", "장", "절", "항", "호", "개", "위", "순위",
               "등", "부", "편", "권", "쪽", "면", "단", "층", "월", "일", "시", "분", "초", "년도", "개소"}
_SAME_COUNTER = {"인": "명", "번": "회", "차례": "회"}          # 같은 것을 세는 다른 이름
# 둘레 낱말로 치지 않는 말 — 어림·범위·모음 말과 한 글자 기능어(「총 4회」의 총은 대상을 말하지 않는다)
_GENERIC_WORDS = {"이상", "이하", "미만", "초과", "내외", "정도", "이내", "가량", "대비", "기준", "모두", "전체", "합계", "구분",
                  "총", "각", "약", "및", "등", "연", "월", "일", "주", "년", "중", "내", "외", "전", "후", "간", "수", "것", "더", "또", "매", "회", "명"}
_HANGUL_RUN = re.compile(r"[가-힣]{1,6}")
_WORD = re.compile(r"[가-힣]+")


def _strip_particle(w: str) -> str:
    changed = True
    while changed and len(w) > 1:
        changed = False
        for p in _PARTICLES:
            if w.endswith(p) and len(w) > len(p):
                w, changed = w[:-len(p)], True
                break
    return w


def _counter_at(text: str, end: int) -> str:
    """숫자 바로 뒤에 붙은 셈 단위 — 띄어 쓰지 않은 한글 덩이에서 조사를 뗀 것(「학년을」→학년). 없으면 표 열 머리의 「(명)」 단위."""
    m = _HANGUL_RUN.match(text, end)
    if m:
        t = _strip_particle(m.group(0))
        return _SAME_COUNTER.get(t, t)
    ls = text.rfind("\n", 0, end) + 1
    if text[ls:end].lstrip().startswith("|") and not re.match(r"[%．.\d]", text[end:end + 1]):
        head = _table_head_cell(text, end)
        u = re.search(r"[(（]\s*([가-힣]{1,3})\s*[)）]", head or "")
        if u and not u.group(1).endswith("원"):
            return _SAME_COUNTER.get(u.group(1), u.group(1))
    return ""


def _table_head_cell(text: str, pos: int) -> str:
    """표 칸 숫자의 같은 열 머리 칸 글(첫 「|」 줄)."""
    ls = text.rfind("\n", 0, pos) + 1
    col = text[ls:pos].count("|") - 1
    head = ls
    while head > 0:
        prev = text.rfind("\n", 0, head - 1) + 1
        if not text[prev:head - 1].lstrip().startswith("|"):
            break
        head = prev
    if head == ls:
        return ""
    he = text.find("\n", head)
    cells = text[head:len(text) if he < 0 else he].strip().strip("|").split("|")
    return cells[col] if 0 <= col < len(cells) else ""


# 「개」 는 무엇이든 세는 말이다 — 모으기 결과(「활동 5개」)도 늘 「개」 로 센다. 그 값을 표에 「5회」「5건」 으로 쓴 것을
# 다른 단위로 막아 「확인 필요」 로 바뀌던 일(11-23 21차 B). 사람(명)·금액·기간 같은 다른 갈래와는 여전히 다르다.
_THING_COUNTERS = {"개", "회", "건", "번", "차례", "곳", "종", "가지"}


def _same_counter(a: str, b: str) -> bool:
    if "개" in (a, b) and a in _THING_COUNTERS and b in _THING_COUNTERS:
        return True
    return a == b or a + "간" == b or b + "간" == a              # 「2주」 = 「2주간」


def _near_words(text: str, start: int, end: int, unit: str, before: int = 14, after: int = 10) -> set[str]:
    """숫자 둘레(같은 줄 앞 before 글자·단위 뒤 after 글자)의 내용 낱말 — 낱말 앞 두 글자(어간)로 견준다(「참여자」≈「참여」).
    표 칸이면 그 행 첫 칸과 열 머리 칸도 둘레로 본다."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le < 0 else le
    seg = text[max(ls, start - before):start] + " " + text[end + len(unit):min(le, end + len(unit) + after)]
    line = text[ls:le]
    if line.lstrip().startswith("|"):
        cells = line.strip().strip("|").split("|")
        seg += " " + (cells[0] if cells else "") + " " + _table_head_cell(text, start)
    else:
        # 「이름: 값, 값」 줄(모으기 결과 「지역 연계 강좌: 기록 14개, 활동 5개」)은 줄 머리 이름이 그 값들의 대상이다
        colon = line.find(":")
        if 0 < colon <= 30 and ls + colon < start and not re.search(r"\d", line[:colon]):
            seg += " " + line[:colon]
    out = set()
    for w in _WORD.findall(seg):
        w = _strip_particle(w)
        if w and w not in _GENERIC_WORDS and w != unit:            # 한 글자 낱말(팀·반·조)도 내용 낱말로
            out.add(w[:2])
    return out


@dataclass(frozen=True)
class _Occ:
    value: str
    unit: str
    words: frozenset
    given: bool
    cell: bool


def _occurrences(evidence_texts: list[str]) -> dict[str, list[_Occ]]:
    """근거 글의 맨숫자(억·만 같은 큰 단위가 붙지 않은 것) 자리마다 (값, 셈 단위, 둘레 낱말, 담당자 글인가)."""
    idx: dict[str, list[_Occ]] = {}
    for ev in evidence_texts:
        is_given = isinstance(ev, Given)
        src = _LIST_MARKER.sub("\n", str(ev or ""))
        for m in _NUM_UNIT.finditer(src):
            if m.group(2):
                continue
            ls = src.rfind("\n", 0, m.start()) + 1
            unit = _counter_at(src, m.end())
            occ = _Occ(m.group(1).replace(",", ""), unit, frozenset(_near_words(src, m.start(), m.end(), unit)),
                       is_given or src[ls:].lstrip().startswith("◇ 집계"),
                       src[ls:m.start()].lstrip().startswith("|"))
            idx.setdefault(occ.value, []).append(occ)
    return idx


def _context_fail(src: str, m: re.Match, occ: dict[str, list[_Occ]], small: bool) -> str | None:
    """맥락이 맞지 않으면 그 수치의 글(「2학년」), 맞으면 None. 금액·%·큰 단위가 붙은 수는 보지 않는다(예전 판정)."""
    if m.group(2):
        return None
    surface = m.group(1).replace(",", "")
    unit = _counter_at(src, m.end())
    if not unit or unit.startswith("원") or src[m.end():m.end() + 1] == "%" or _money_value(src, m, draft=True):
        return None
    label = m.group(1) + unit
    found = occ.get(surface, [])
    if small:
        words = _near_words(src, m.start(), m.end(), unit)
        for o in found:
            if o.unit and _same_counter(o.unit, unit) and (o.given or (words & o.words)):
                return None
            if not o.unit and (o.cell or o.given) and (words & o.words):   # 단위 없는 표 칸·집계 줄은 행·열·항목 이름이 겹칠 때만
                return None
        return label
    if not found or any(not o.unit or _same_counter(o.unit, unit) for o in found):
        return None                                                  # 단위 환산으로 맞은 값(근거 쪽 맨숫자 없음)·단위 없는 근거 값은 예전처럼
    return label


FLAG = "(근거 확인 필요)"

# ---------------------------------------------------------------- 셈으로 나온 값
# 담당자가 준 값(월급·생년월)을 규정의 식에 넣어 셈한 결과는 근거 글에 없다. 셈을 「식 = 결과」 로 보이면 여기서 다시 셈해
# 맞을 때만 결과를 근거 있는 값으로 본다 — 틀린 셈의 결과와, 식의 근거 없는 항(지어낸 월급 등)은 그대로 표시된다(11-06 명예퇴직금 계산).
_OPERAND = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:조|억|천만|백만|십만|만|천)?\s*(?:원|%|퍼센트|개월|월|년|명|일|배|회|주|시간|건|개|곳|팀|학점)?"
# 이어진 금액(「1억 800만」)은 앞 덩이가 큰 단위로 끝날 때만 잇는다 — 숫자를 여러 갈래로 나눠 보는 되짚기 폭주(ReDoS)를 막는다
_BIG = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?(?:조|억|천만|백만|십만|만|천)"
_AMOUNT = r"(?:" + _BIG + r"\s?)*" + _OPERAND
_OP = r"\s*[×xX*÷/+\-−]\s*"
_CALC = re.compile(r"(" + _AMOUNT + r"(?:" + _OP + _AMOUNT + r")+)\s*[=＝]\s*(?:약\s*)?(" + _AMOUNT + r")")
# 기간 셈 — 「2034년 3월 ~ 2035년 8월 = 18개월」
_SPAN = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월(?:\s*\d{1,2}\s*일)?\s*(?:부터|~|∼|-|－|에서)\s*"
                   r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월(?:\s*\d{1,2}\s*일)?\s*(?:까지)?\s*[=＝:]?\s*(?:약\s*)?(\d+)\s*개월")


# 날짜 더하기·빼기 — 「2014년 3월 + 20년 = 2034년 3월」 「1970년 7월 + 65세 = 2035년 7월」
_DATE_ADD = re.compile(r"((?:19|20)\d{2})\s*년(?:\s*(\d{1,2})\s*월)?(?:\s*\d{1,2}\s*일)?(?:생)?\s*([+\-−])\s*(?:만\s*)?(\d{1,3})\s*(?:년|세|살)"
                       r"(?:\s*(\d{1,2})\s*개월)?\s*[=＝]\s*((?:19|20)\d{2})\s*년(?:\s*(\d{1,2})\s*월)?")


def wrong_dates(text: str) -> list[tuple[int, int]]:
    """틀린 날짜 셈의 결과 자리 — 연도는 수치 점검에서 빼므로 셈으로 적힌 것만 따로 맞춰 본다."""
    bad = []
    for m in _DATE_ADD.finditer(text or ""):
        y, mo = int(m.group(1)), int(m.group(2) or 0)
        sign = -1 if m.group(3) in "-−" else 1
        n, extra = int(m.group(4)), int(m.group(5) or 0)
        months = y * 12 + (mo or 1) - 1 + sign * (n * 12 + extra)
        want_y, want_m = divmod(months, 12)
        got_y, got_m = int(m.group(6)), int(m.group(7) or 0)
        if got_y != want_y or (mo and got_m and got_m != want_m + 1):
            bad.append((m.start(6), m.end()))
    return bad


def _amount_value(s: str):
    """「3억 3,750만 원」·「75%」·「18개월」 의 값(Fraction) — 이어진 단위는 합산, % 는 100 으로 나눈다."""
    from fractions import Fraction
    total, pct, any_ = Fraction(0), False, False
    for m in _NUM_UNIT.finditer(s):
        total += Fraction(m.group(1).replace(",", "")) * _UNIT_VALUE.get(m.group(2) or "", 1)
        any_ = True
    if not any_:
        return None
    if re.search(r"%|퍼센트", s):
        pct = True
    return total / 100 if pct else total


def _eval_calc(expr: str):
    from fractions import Fraction
    parts = re.split(r"\s*([×xX*÷/+\-−])\s*", expr.strip())
    vals, ops = [], []
    for i, p in enumerate(parts):
        if i % 2:
            ops.append(p)
        else:
            v = _amount_value(p)
            if v is None:
                return None
            vals.append(v)
    # 곱·나눗셈 먼저
    acc, out_ops, cur = [], [], vals[0]
    for op, v in zip(ops, vals[1:]):
        if op in "×xX*":
            cur = cur * v
        elif op in "÷/":
            if v == 0:
                return None
            cur = cur / v
        else:
            acc.append(cur); out_ops.append(op); cur = v
    acc.append(cur)
    res = acc[0]
    for op, v in zip(out_ops, acc[1:]):
        res = res + v if op == "+" else res - v
    return res


def _calc_iter(src: str):
    """셈 줄만 — 400자 넘는 줄은 보지 않는다(검사 시간 상한)."""
    pos = 0
    for line in src.split("\n"):
        if len(line) <= 400 and ("=" in line or "＝" in line):
            for m in _CALC.finditer(line):
                yield _Shift(m, pos)
        pos += len(line) + 1


class _Shift:
    """줄 안 일치를 글 전체 자리로 옮긴 것."""
    def __init__(self, m, off):
        self._m, self._o = m, off

    def group(self, i):
        return self._m.group(i)

    def start(self, i=0):
        return self._m.start(i) + self._o

    def end(self, i=0):
        return self._m.end(i) + self._o


_PCT_CELL = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*(%)?\s*$")
_CELL_NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def _row_ratios(src: str) -> tuple[list[tuple[int, int]], set[str]]:
    """표 한 행에서 비율 칸(「90.0%」, 또는 열 머리가 「달성률(%)」·「비율」 인 칸의 수)이 같은 행의 다른 두 수의 a/b×100 과
    보인 자릿수까지 맞으면 셈한 값으로 본다 — 실적 9건·목표 10건의 달성률 90.0% 를 근거 없는 수로 막던 것(11-21 결과보고서 3차).
    두 수 자체는 따로 대조된다."""
    spans: list[tuple[int, int]] = []
    vals: set[str] = set()
    pos = 0
    for line in src.split("\n"):
        ls, pos = pos, pos + len(line) + 1
        if not line.lstrip().startswith("|") or line.count("|") < 3:
            continue
        cells, at = [], ls
        for part in line.split("|"):
            cells.append((at, part))
            at += len(part) + 1
        # 비율 칸 = 「%」 가 붙었거나 열 머리가 률·율·% 인 칸. 단위 없는 「300」「264」 도 수 모양은 같아 피연산자에서 빠지던 것(11-22 6차: 달성률 88.0% 막힘)
        pct = []
        for off, c in cells:
            m = _PCT_CELL.match(c)
            if m and (m.group(2) or re.search(r"%|률|율|비율", _table_head_cell(src, off + 1) or "")):
                pct.append((off, m))
        pct_offs = {off for off, _ in pct}
        nums = []
        for off, c in cells:
            m = _CELL_NUM.search(c)
            if m and off not in pct_offs:
                try:
                    nums.append(Decimal(m.group(0).replace(",", "")))
                except Exception:
                    pass
        if len(nums) < 2:
            continue
        for off, m in pct:
            p = Decimal(m.group(1))
            q = Decimal(1).scaleb(-len(m.group(1).partition(".")[2]))
            ok = any(b and abs((a / b * 100).quantize(q) - p) <= q / 2 + Decimal("0.0001")
                     for a in nums for b in nums if a is not b)
            if ok:
                spans.append((off + m.start(1), off + m.end(1)))
                vals.add(_fmt(p))
    return spans, vals


_TOTAL_ROW = re.compile(r"^\s*(?:합\s*계|총\s*계|소\s*계|계|전\s*체|총\s*합)\s*$")


def _column_totals(src: str) -> tuple[list[tuple[int, int]], set[str]]:
    """표의 합계 행(첫 칸 「합계·계·소계」 이거나 마지막 행) 값이 같은 열 다른 자료 행들의 합과 맞으면 셈한 값으로 본다 —
    11-23 시연 04: 프로그램별 참여 인원은 넣고 합계 칸만 「확인 필요」 로 남았다(관문이 손셈으로 막을 것을 피함). 더하는 값들은 따로 대조된다.
    정수는 꼭 같아야, 소수(비율 합 100.0)는 보인 자릿수 반올림까지."""
    spans: list[tuple[int, int]] = []
    vals: set[str] = set()
    lines, pos = [], 0
    for line in src.split("\n"):
        lines.append((pos, line))
        pos += len(line) + 1
    blocks, cur = [], []
    for ls, line in lines:
        if line.lstrip().startswith("|") and line.count("|") >= 3:
            cur.append((ls, line))
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    for blk in blocks:
        rows = []
        for ls, line in blk:
            if re.fullmatch(r"\s*\|[\s\-:|]+\|?\s*", line):
                continue
            cells, at = [], ls
            for part in line.split("|")[1:]:
                at += 1
                cells.append((at, part))
                at += len(part)
            rows.append(cells)
        if len(rows) < 4:                                    # 머리 + 자료 둘 이상 + 합계
            continue
        data = rows[1:]
        tot_idx = [i for i, r in enumerate(data) if r and _TOTAL_ROW.match(r[0][1])] or [len(data) - 1]
        for ti in tot_idx:
            for j in range(1, len(data[ti])):
                off, c = data[ti][j]
                m = _CELL_NUM.search(c)
                if not m:
                    continue
                try:
                    v = Decimal(m.group(0).replace(",", ""))
                    adds = [Decimal(mm.group(0).replace(",", "")) for i, r in enumerate(data) if i not in tot_idx and j < len(r)
                            for mm in [_CELL_NUM.search(r[j][1])] if mm]
                except Exception:
                    continue
                if len(adds) < 2:
                    continue
                q = Decimal(1).scaleb(-len(m.group(0).partition(".")[2])) if "." in m.group(0) else Decimal(0)
                if abs(sum(adds, Decimal(0)) - v) <= q / 2 + (Decimal("0.0001") if q else 0):
                    spans.append((off + m.start(), off + m.end()))
                    vals.add(_fmt(v))
    return spans, vals


def derived_spans(text: str) -> tuple[list[tuple[int, int]], set[str]]:
    """맞게 셈한 결과의 자리와 값 — (결과 글의 (시작, 끝) 목록, 결과 정준값). 반올림(「약」)은 0.5% 까지 같은 값으로 본다."""
    spans: list[tuple[int, int]] = []
    vals: set[str] = set()
    src = text or ""
    for m in _calc_iter(src):
        got, want = _eval_calc(m.group(1)), _amount_value(m.group(2))
        if got is None or want is None:
            continue
        if abs(want - got) <= max(abs(got) / 200, 0.5):
            spans.append((m.start(2), m.end(2)))
            vals.add(_fmt(Decimal(want.numerator) / Decimal(want.denominator)))   # 결과 전체 값만(「1억 800만」 의 800 따로는 아니다)
    sp_, va_ = _row_ratios(src)
    spans += sp_
    vals |= va_
    sp_, va_ = _column_totals(src)
    spans += sp_
    vals |= va_
    for m in _SPAN.finditer(src):
        y1, m1, y2, m2, n = (int(m.group(i)) for i in range(1, 6))
        diff = (y2 - y1) * 12 + (m2 - m1)
        if 1 <= m1 <= 12 and 1 <= m2 <= 12 and abs(diff - n) <= 1:     # 그 달을 넣고 빼는 셈법 차이까지
            spans.append((m.start(5), m.end(5)))
            vals.add(str(n))
    return spans, vals


def _won_text(v, like: str) -> str:
    """셈 결과를 모델이 쓴 꼴(억·만·맨숫자)로 — 4500만 → 「4,500만」, 1.08억 → 「1억 800만」."""
    from fractions import Fraction
    v = Fraction(v)
    if v.denominator != 1 and "%" not in like:
        v = Fraction(round(v))
    n = int(v) if v.denominator == 1 else None
    if n is None:
        return _fmt(Decimal(v.numerator) / Decimal(v.denominator))
    if "억" in like or ("만" in like and n >= 10**8):
        eok, rest = divmod(n, 10**8)
        man = rest // 10**4
        return f"{eok:,}억" + (f" {man:,}만" if man else "")
    if "만" in like and n % 10**4 == 0:
        return f"{n // 10**4:,}만"
    return f"{n:,}"


def fix_calculations(text: str) -> tuple[str, list[str]]:
    """틀린 셈(「500만 원 × 75% × 12개월 = 6,750만 원」)의 결과를 다시 셈한 값으로 바꾼다 — 같은 틀린 값을 글의 다른 곳(결론)에 쓴 것도.
    돌려주는 것은 (새 글, 「틀린 값 → 바른 값」 목록). 날짜·기간은 손대지 않는다(점검 표시만)."""
    src = text or ""
    fixes: dict[str, str] = {}
    for m in _calc_iter(src):
        got, want = _eval_calc(m.group(1)), _amount_value(m.group(2))
        if got is None or want is None or "%" in m.group(2) or abs(want - got) <= max(abs(got) / 200, 0.5):
            continue
        whole = m.group(1) + m.group(2)
        if re.search(r"(?:19|20)\d{2}\s*년|\d\s*월(?!급)|\d\s*일|\d\s*년", whole) and not re.search(r"원", m.group(2)):
            continue                                         # 날짜·기간 셈(「2014년 3월 + 20년」)은 금액 셈이 아니다 — 바꾸지 않는다(날짜는 wrong_dates 가 표시)
        num = re.match(r"[\d,.\s조억천백십만]+?(?=\s*(?:원|개월|월|년|명|일|배)?\s*$)", m.group(2).strip())
        wrong = (num.group(0) if num else m.group(2)).strip()
        fixes[wrong] = _won_text(got, wrong)
    out = src
    for wrong, right in fixes.items():
        out = re.sub(r"(?<![\d,])" + re.escape(wrong) + r"(?![\d,])", right, out)
    return out, [f"{w} → {r}" for w, r in fixes.items()]


def supported_calcs(text: str, evidence_texts: list[str]) -> list[str]:
    """답 안의 셈(「A × B = C」) 가운데 셈에 들어간 수가 모두 근거에 있는 것 — 그 셈 글 자체를 근거로 더해 결과(C)를 근거 있는 값으로 본다.
    셈이 맞는지는 fix_calculations 가 먼저 바로잡는다. 근거 수치끼리 셈한 값(만 나이·합계)에 「근거 확인 필요」가 붙던 일(11-10)."""
    joined = re.sub(r"[,\s]", "", "\n".join(evidence_texts or []))
    out = []
    for m in _calc_iter(text or ""):
        nums = [n.replace(",", "") for n in _NUMBER.findall(m.group(1))]
        if nums and all(n in joined for n in nums):
            out.append(m.group(0))
    return out


def flag_unsupported(text: str, evidence_texts: list[str]) -> tuple[str, list[str]]:
    """근거에 없는 수치 바로 뒤에 「(근거 확인 필요)」를 붙인다 — 대화 답은 수치를 지우지 않고, 어느 수치를 원문에서 확인해야 하는지
    보이게 한다(문서 초안은 mask_unsupported 로 가린다). 판정은 mask_unsupported 와 같다. 돌려주는 것은 (새 글, 표시한 수치).
    근거 수치끼리 셈한 결과(supported_calcs)는 근거가 있는 값으로 본다."""
    ev = list(evidence_texts or [])
    return _rewrite_unsupported(text, ev + supported_calcs(text, ev), mark=True)


_MD = re.compile(r"(0?[1-9]|1[0-2])\.(0?[1-9]|[12]\d|3[01])")
_RANGE_SEP = "~∼〜–—-"
_WEEKDAY_AFTER = re.compile(r"\.?\s*[(（]\s*[월화수목금토일]")


def _month_day(src: str, m: re.Match) -> bool:
    """「11.30」「6.25」처럼 월.일로 쓴 날짜 덩이인가 — 기간 기호(~)·요일 괄호·「월」 같은 날짜 맥락이 붙은 것만(소수 4.95 는 아님).
    날짜는 수치가 아니다 — 근거 표시가 날짜·기간 중간에 끼어 「11.30(근거 확인 필요).~12.1.」처럼 깨지지 않게(11-10 대화 174)."""
    surface = m.group(1)
    if not _MD.fullmatch(surface):
        return False
    after = src[m.end(1):m.end(1) + 4]
    before = src[max(0, m.start(1) - 3):m.start(1)].rstrip()
    if re.match(r"\.?\s*[" + re.escape(_RANGE_SEP) + "]", after) or (before and before[-1] in _RANGE_SEP):
        return True
    if _WEEKDAY_AFTER.match(after):
        return True
    return False


_EV_DATE = re.compile(r"(?<!\d)(0?[1-9]|1[0-2])\s*월\s*(0?[1-9]|[12]\d|3[01])\s*일|(?:19|20)\d{2}\s*[.\-/]\s*(0?[1-9]|1[0-2])\s*[.\-/]\s*(0?[1-9]|[12]\d|3[01])(?!\d)")


def evidence_dates(evidence_texts: list[str]) -> set[str]:
    """근거 글의 월·일(「11월 10일」「2026.11.10」) — 「11.10.」 으로 줄여 쓴 날짜를 수치로 보지 않게."""
    out = set()
    for ev in evidence_texts:
        for m in _EV_DATE.finditer(ev or ""):
            mo, d = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            out.add(f"{int(mo)}.{int(d)}")
    return out


def _norm_md(surface: str) -> str:
    mo, d = surface.split(".")
    return f"{int(mo)}.{int(d)}"


def b_end(spans, pos):
    return next(b for a, b in spans if a <= pos < b)


_SEC_HEAD = re.compile(r"(?m)^(\s*)(\d{1,2}(?:\.\d{1,2})+)(\.?\s+[가-힣A-Za-z])")


def _rewrite_unsupported(text: str, evidence_texts: list[str], mark: bool, context: bool = False) -> tuple[str, list[str]]:
    # 줄 머리 절 번호(「2.2 분야별 추진 실적」)는 값의 근거가 아니다 — 「달성률 2.2%」 가 절 번호 2.2 로 통과했다(11-23)
    allowed_surface, allowed_canonical, allowed_money = _allowed([_SEC_HEAD.sub(r"\1§\3", e or "") for e in evidence_texts])
    occ = _occurrences(evidence_texts) if context else {}
    out, masked, last = [], [], 0
    src = text or ""
    spans, derived = derived_spans(src)                  # 맞게 셈한 결과는 근거 있는 값(같은 값을 뒤에 다시 써도)
    allowed_canonical = allowed_canonical | derived
    if derived:
        # 셈한 값을 다음 식에 다시 쓴 이어진 금액(「2억 2,500만 원 + …」)은 합친 값으로 본다 — 「2억」「2,500만」 을 따로 보면 근거 없음이 된다
        for am in re.finditer(r"(?:" + _BIG + r"\s?)+(?:\d{1,3}(?:,\d{3})+|\d+)?", src):
            v = _amount_value(am.group(0))
            if v is not None and _fmt(Decimal(v.numerator) / Decimal(v.denominator)) in derived:
                spans.append((am.start(), am.end()))
    bad_dates = wrong_dates(src) if mark else []         # 틀린 날짜 셈 — 연도라도 표시(대화 답만)
    # 근거(작업본 글 포함)에서 줄 머리 절 번호(「2.2 분야별 추진 실적」) — 본문의 「2.2표」「2.1및」 은 절을 가리키는 말이다(11-23: 관문이
    # 맥락 대조로 막아 「2. 추진 실적」 절이 두 번 거절당했다)
    ev_dates = evidence_dates(evidence_texts)            # 근거에 있는 날짜를 「11.10.」 처럼 줄여 쓴 것은 날짜다(11-23 21차 B)
    sec_nos = {m_.group(2) for ev in evidence_texts for m_ in _SEC_HEAD.finditer(ev or "")}
    for m in _NUM_UNIT.finditer(src):
        if any(a <= m.start() < b for a, b in spans):
            continue
        surface = m.group(1).replace(",", "")
        unit = m.group(2)
        line_start = src.rfind("\n", 0, m.start()) + 1
        after = src[m.end():m.end() + 2]
        if src[line_start:m.start()].strip() == "" and re.match(r"[.)]\s", after):
            continue                                          # 목록 번호 「1. 」「2) 」
        if m.start() < last:
            continue
        if any(a <= m.start() < b for a, b in bad_dates):
            end = b_end(bad_dates, m.start())
            out.append(src[last:end] + FLAG)
            masked.append(src[m.start():end].strip())
            last = end
            continue
        if _YEAR_LIKE.match(surface):
            continue
        if "." in surface and surface in sec_nos and not unit and src[m.end():m.end() + 1] != "%" \
                and _counter_at(src, m.end()) not in ("명", "원", "건", "회", "개", "곳", "점", "배", "시간"):
            continue                                          # 절 번호를 가리키는 말(「2.2표」「2.1 및」)
        if _month_day(src, m) or (not unit and _MD.fullmatch(surface) and _norm_md(surface) in ev_dates
                                  and re.match(r"\.?\s*[),·]|\.\s|\.$", src[m.end(1):m.end(1) + 2] or ".")):
            continue                                          # 월.일 날짜(「11.30.~12.1.」「6.25(월)」) — 날짜는 수치 점검 대상이 아니다
        if re.fullmatch(r"\d{2}(?:\.(?:0?[1-9]|1[0-2]))?", surface) and src[max(0, m.start() - 1):m.start()] in ("’", "'", "‘"):
            continue                                          # 줄인 연도 「’26」「’26.9월」 — 날짜다
        before, after1 = src[max(0, m.start() - 1):m.start()], src[m.end(1):m.end(1) + 2]
        if (before in ("-", "/", ":", "~", ".") and m.start() >= 2 and src[m.start() - 2].isdigit()) or re.match(r"[-/:]\d", after1) \
                or re.match(r"\.\d", after1):
            continue                                          # 전화번호·날짜·코드(010-1234-5678, 2026.4.3, 3/4)는 수치가 아니다
        tail = src[m.end():m.end() + 1]
        try:
            small = Decimal(surface) <= 10 and not unit and tail != "%"
        except Exception:
            small = False
        ctx_label = None
        if small:
            if not context or Decimal(surface) <= 1 or _counter_at(src, m.end()) in _STRUCTURAL:
                continue                                      # 구조 숫자·날짜 단위·「1」 은 예전처럼 보지 않는다
            ctx_label = _context_fail(src, m, occ, small=True)
            if ctx_label is None:
                continue
        elif _supported(src, m, allowed_surface, allowed_canonical, allowed_money):
            ctx_label = _context_fail(src, m, occ, small=False) if context else None
            if ctx_label is None:
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
        masked.append(ctx_label or m.group(0).strip())
    out.append(src[last:])
    return "".join(out), masked
