"""표 갈래 가리기와 갈래별 디자인 기본값 — 무엇을 표로 쓸지는 27B 가 정하고, 그 표가 어떤 꼴로 보일지는 여기서 정한다.

갈래는 표의 머리줄 낱말·열 값의 꼴(날짜·기간, 금액, 수, 비율, 기호, 짧은 라벨, 긴 글)·행 수·첫 열 성격 같은 일반 특징으로만 가린다.
특정 문서·사업·표 이름은 보지 않는다.

  summary   라벨-값 요약표 — 머리줄 없이 2열(라벨|값)이나 4열(라벨|값|라벨|값)
  schedule  일정표 — 달·분기 열이 여럿이거나, 일정·기간 머리 아래 날짜·기간 값
  budget    예산·금액표 — 금액 열(머리가 금액·원 단위이거나 값이 큰 수), 흔히 합계 행
  stats     수치 현황표 — 첫 열 뒤 열의 절반 넘게 수·비율
  roles     역할·담당표 — 담당·역할·소속·성명·부서 같은 머리
  compare   비교표 — 현행·개선, 기존·변경, 전·후, 장점·단점 같은 맞선 머리
  list      목록형 — 첫 열이 차례 번호이거나 긴 글 열이 있는 표
  general   그 밖

디자인 값의 고르는 차례는 양식 견본 표 → 실문서에서 캔 갈래 값(app.table_styles, scripts/230) → 여기 기본값(KIND_DEFAULT).
기본값은 학교 표 공통 모양(house_style.HOUSE·TABLE) 위에 갈래마다 다른 점만 얹는다.
"""
from __future__ import annotations

import re

from zzaimy.export import house_style as hs

KINDS = ("summary", "schedule", "budget", "stats", "roles", "compare", "list", "general")
KIND_NAMES = {"summary": "라벨-값 요약표", "schedule": "일정표", "budget": "예산·금액표", "stats": "수치 현황표",
              "roles": "역할·담당표", "compare": "비교표", "list": "목록형", "general": "일반 표"}
# 열 값 꼴 — 열마다 본문 칸 값의 다수 꼴
COL_TYPES = ("empty", "symbol", "date", "percent", "money", "number", "label", "text", "long")

_SYMBOL = re.compile(r"^[○●◎◯△▲▽▼□■◇◆☆★✓✔√VvOoXx\-–—─━·ㆍ→←↔⇒▶◀►◄▷◁=*]+$")   # 표시·일정 막대 기호
_PERCENT = re.compile(r"^[-+△▲▽]?\s*\d+(?:\.\d+)?\s*%p?$")
_NUMBER = re.compile(r"^[-+△▲▽±]?\s*\d+(?:\.\d+)?\s*(?:명|개|건|회|점|배|개소|과|팀|종|권|편|시간|일|주|학점|천원|만원|원)?$")
_DATE = re.compile(
    r"^(?:(?:'\d{2}|\d{4})\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]?(?:\s*\d{1,2}\s*[.일]?)?"   # 2025.3. · 2025-03-02 · '25.3 · 2025년 3월
    r"|\d{1,2}\s*\.\s*\d{1,2}\s*\.|\d{1,2}\s*/\s*\d{1,2}"                         # 3.15. · 03/15(끝 점 없는 3.5 는 소수)
    r"|\d{1,2}\s*[~∼\-–]\s*\d{1,2}\s*(?:월|분기|주차?|학기)"                        # 3~6월
    r"|\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"                                          # 3월 · 3월 15일
    r"|(?:19|20)\d{2}\s*(?:년|학년도|년도)?"                                      # 2025 · 2025년
    r"|\d\s*/\s*4\s*분기|\d\s*분기|[상하]반기|\d\s*학기|\d{1,2}\s*주(?:차)?)"
    r"(?:\s*[~∼\-–]\s*.{0,14})?$")                                                 # 기간(~ 뒤 끝 값)
_MONTH_HEAD = re.compile(r"^(?:\d{1,2}\s*월?|\d\s*/\s*4|\d\s*분기|[상하]반기|\d\s*학기|\d{1,2}\s*주(?:차)?|'?\d{2}\.\d{1,2}\.?)$")
_SCHED_HEAD = re.compile(r"일정|기간|시기|일자|날짜|기한|월별|추진\s*시기|운영\s*기간|소요\s*기간|마감")
_ROLE_HEAD = re.compile(r"담당|역할|소속|직위|직급|직책|성명|이름|부서|책임자|구성원|위원|참여\s*인력|연락처|전화|전공|분야")
_COMPARE_HEAD = re.compile(r"현행|개선|기존|변경|^전$|^후$|이전|이후|대비|비교|차이|장점|단점|강점|약점|AS-?IS|TO-?BE|before|after|안\s*\d$|[A-C]안",
                           re.I)
_ORDER_HEAD = re.compile(r"^(?:순번|번호|연번|순서|차례|No\.?|NO\.?|#)$")
_ORDER_VAL = re.compile(r"^(?:\d{1,3}|[①-⑳]|[가-하]|[ⅰ-ⅹⅠ-Ⅹ]+)[.)]?$")


def _clean(t) -> str:
    return " ".join(str(t or "").split())


def is_code(text: str) -> bool:
    """프로그램 코드 칸인가 — 영문·기호가 대부분이고 코드 꼴(예약어 줄·함수 부르기·대입·중괄호)이 있다. 「print()」 「for i in range(1, 101):」"""
    t = (text or "").strip()
    if len(t) < 3 or re.search(r"[가-힣]", t.split("#", 1)[0]):
        return False
    from zzaimy.verify.numbers import _CALL, _CODE_LINE
    return bool(_CODE_LINE.fullmatch(t) or _CALL.search(t) or re.search(r"\w\(\)|[{};]|\w\s*[+\-*/]?=\s*\S|=>|->", t))


def value_type(text: str, head: str = "") -> str:
    """칸 값 하나의 꼴."""
    t = _clean(text)
    if not t:
        return "empty"
    if is_code(text):
        return "code"
    if _SYMBOL.fullmatch(t):
        return "symbol"
    if _PERCENT.fullmatch(t):
        return "percent"
    # 일정표에 흔한 꼴 — 날짜 뒤 요일 「(목)」·시각 「10:00~12:00」 은 떼고 본다(「11. 20.(목)」 을 짧은 글로 보던 것, 11-15 실사용 확인)
    d = re.sub(r"\s*\((?:월|화|수|목|금|토|일)\)|\s*\d{1,2}:\d{2}(?:\s*[~∼-]\s*\d{1,2}:\d{2})?", "", t).strip()
    if d and (_DATE.fullmatch(d) and not hs.is_amount(d.replace(" ", "")) or re.fullmatch(r"(?:19|20)\d{2}(?:년|학년도|년도)?", d)):
        return "date"
    if head and _SCHED_HEAD.search(head) and re.fullmatch(r"\d{1,2}\s*일\s*차|\d{1,2}\s*차시|오전|오후|종일|\d{1,2}\s*주\s*차", t):
        return "date"
    if hs.is_amount(t) or (hs.money_head(head) and re.fullmatch(r"[-+△▲▽]?[\d,.]+", t)):
        return "money"
    if _NUMBER.fullmatch(t):
        return "number"
    u = hs.text_units(t)
    return "label" if u <= 10 else "text" if u <= 25 else "long"


def column_type(values: list[str], head: str = "") -> str:
    """열 값 꼴 — 빈칸을 뺀 값의 다수 꼴(수 갈래는 셋이 섞여도 수로 본다). 글 갈래는 평균 길이로."""
    vt = [value_type(v, head) for v in values]
    vt = [x for x in vt if x != "empty"]
    if not vt:
        return "empty"
    n = len(vt)
    cnt = {k: vt.count(k) for k in set(vt)}
    best = max(cnt, key=lambda k: (cnt[k], k))
    if cnt[best] / n >= 0.6:
        if best in ("label", "text", "long"):
            avg = sum(hs.text_units(_clean(v)) for v in values if _clean(v)) / n
            return "label" if avg <= 10 else "text" if avg <= 25 else "long"
        return best
    numeric = sum(cnt.get(k, 0) for k in ("money", "number", "percent"))
    if numeric / n >= 0.6:
        return "money" if cnt.get("money", 0) >= max(cnt.get("number", 0), cnt.get("percent", 0)) else (
            "percent" if cnt.get("percent", 0) > cnt.get("number", 0) else "number")
    avg = sum(hs.text_units(_clean(v)) for v in values if _clean(v)) / n
    return "label" if avg <= 10 else "text" if avg <= 25 else "long"


def head_rows_of(rows: list[list[str]]) -> int:
    """머리줄 수 어림 — 첫 행이 모두 글이고(두 칸 이상), 수·금액·날짜 꼴 값이 아니며, 아래에 행이 있으면 1."""
    if len(rows) < 2:
        return 0
    first = [_clean(c) for c in rows[0]]
    if len(first) < 2 or not all(first):
        return 0
    if sum(value_type(c) in ("money", "number", "percent") for c in first) > len(first) // 2:
        return 0
    # 2열 표의 첫 행이 「라벨 | 긴 값」 이면 요약표 첫 줄이지 머리줄이 아니다
    # 머리 낱말은 짧은 이름씨라, 2열 표에서 첫 행에 긴 글·숫자가 있으면 머리줄로 보지 않는다(「사업명 | 지역혁신 인재양성」)
    if len(first) == 2 and any(hs.text_units(c) > 8 or re.search(r"\d", c) for c in first):
        return 0
    return 1


def _norm(rows) -> list[list[str]]:
    rows = [[_clean(c) for c in (r if isinstance(r, (list, tuple)) else [r])] for r in (rows or [])]
    n = max((len(r) for r in rows), default=0)
    return [r + [""] * (n - len(r)) for r in rows]


def features(rows: list[list[str]], head_rows: int | None = None) -> dict:
    """갈래 판정의 재료 — 머리줄 수, 열 머리 글, 열 값 꼴, 합계 행, 첫 열 성격."""
    rows = _norm(rows)
    n_rows, n_cols = len(rows), max((len(r) for r in rows), default=0)
    hr = head_rows_of(rows) if head_rows is None else max(0, min(int(head_rows), n_rows))
    heads = [" ".join(rows[r][c] for r in range(hr) if rows[r][c]).strip() for c in range(n_cols)]
    body = rows[hr:]
    total_rows = [i + hr for i, r in enumerate(body) if r and hs.is_total(r[0])]
    data = [r for i, r in enumerate(body) if i + hr not in total_rows]
    ctypes = [column_type([r[c] for r in data] or [r[c] for r in body], heads[c]) for c in range(n_cols)]
    first_vals = [r[0] for r in data if r and r[0]]
    ordered = bool(first_vals) and (sum(bool(_ORDER_VAL.fullmatch(v)) for v in first_vals) / len(first_vals) >= 0.8
                                    or bool(_ORDER_HEAD.fullmatch(heads[0] if heads else "")))
    return {"rows": n_rows, "cols": n_cols, "head_rows": hr, "heads": heads, "types": ctypes,
            "total_rows": total_rows, "ordered": ordered}


def classify(rows: list[list[str]], head_rows: int | None = None) -> tuple[str, dict]:
    """(갈래, 재료) — 위 차례대로 먼저 맞는 갈래. 한 열 표(상자)는 general."""
    f = features(rows, head_rows)
    n, hr, heads, ty = f["cols"], f["head_rows"], f["heads"], f["types"]
    if n < 2 or f["rows"] < 1:
        return "general", f
    rest = ty[1:]
    numeric = [t in ("money", "number", "percent") for t in rest]
    if hr == 0 and n in (2, 4) and ty[0] in ("label", "text") and (n == 2 or ty[2] in ("label", "text")):
        return "summary", f
    month_heads = sum(bool(_MONTH_HEAD.fullmatch(h)) for h in heads)
    symbol_cols = sum(t in ("symbol", "empty") for t in rest)
    if month_heads >= 3 or (hr and symbol_cols >= 3 and month_heads >= 2):
        return "schedule", f
    if any(_SCHED_HEAD.search(h) and t == "date" for h, t in zip(heads, ty)) or (
            "date" in ty and not any(t == "money" for t in ty) and sum(numeric) <= len(rest) // 2 and n <= 5):
        return "schedule", f
    if any(t == "money" for t in ty) and (f["total_rows"] or any(hs.money_head(h) for h in heads) or ty.count("money") >= 2):
        return "budget", f
    if rest and sum(numeric) >= max(1, (len(rest) + 1) // 2) and sum(numeric) >= min(2, len(rest)):
        return "stats", f
    if sum(bool(_ROLE_HEAD.search(h)) for h in heads) >= 1 and hr:
        return "roles", f
    if sum(bool(_COMPARE_HEAD.search(h)) for h in heads) >= 2 or (hr and sum(bool(_COMPARE_HEAD.search(h)) for h in heads[1:]) >= 1
                                                                    and n in (3, 4) and all(t in ("label", "text", "long", "empty") for t in rest)
                                                                    and re.fullmatch(r"구분|항목|비교\s*항목|내용", heads[0] or "")):
        return "compare", f
    if f["ordered"] or "long" in ty:
        return "list", f
    return "general", f


# ── 갈래별 디자인 기본값 ── 키 뜻: head_fill 머리줄 음영(빈 값·none 은 음영 없음), head_bold·head_align, head_pt·body_pt(None 이면 HOUSE font_pt),
# label_fill·label_align(첫 열 라벨 칸 — 요약표·비교표), align(열 값 꼴 → 정렬, 없으면 house_style.value_align), total_fill·total_bold,
# outer·inner(선 굵기), head_rule(머리줄 아래 선 SOLID|DOUBLE|THIN), row_h(새 표 행 최소 높이 HWPUNIT), col_mm(열 값 꼴 → 바라는 폭 mm, 비율로만 씀)
_BASE_ALIGN = {"symbol": "CENTER", "date": "CENTER", "percent": "CENTER", "number": "CENTER", "money": "RIGHT",
               "label": "CENTER", "text": "LEFT", "long": "LEFT", "empty": ""}
_BASE_MM = {"symbol": 10.0, "date": 24.0, "percent": 16.0, "number": 16.0, "money": 24.0, "label": 26.0, "text": 45.0,
            "long": 80.0, "empty": 14.0}
BASE = {"head_fill": hs.HOUSE["head_fill"], "head_bold": True, "head_align": "CENTER", "head_pt": None, "body_pt": None,
        "label_fill": "none", "label_align": "CENTER", "label_bold": False,
        "align": dict(_BASE_ALIGN), "total_fill": hs.TABLE["total_fill"], "total_bold": True,
        "outer": hs.HOUSE["outer"], "inner": hs.HOUSE["inner"], "head_rule": "SOLID", "row_h": hs.MIN_ROW_HEIGHT,
        "col_mm": dict(_BASE_MM)}
KIND_DEFAULT: dict[str, dict] = {
    # 요약표 — 머리줄 없음, 라벨 열 음영·가운데, 값은 왼쪽(긴 글이 많다)
    "summary": {"label_fill": hs.HOUSE["label_fill"], "align": {"label": "LEFT", "number": "LEFT", "money": "LEFT", "date": "LEFT", "percent": "LEFT"}},
    # 일정표 — 기간·기호 칸 가운데, 달 칸은 좁게
    "schedule": {"align": {"label": "LEFT", "text": "LEFT"}, "col_mm": {"symbol": 8.0, "empty": 8.0}},
    # 예산표 — 금액 오른쪽, 합계 행 연회색·굵게
    "budget": {"align": {"money": "RIGHT", "number": "RIGHT", "label": "LEFT"}},
    # 수치 현황표 — 수 칸 가운데(학교 현황표의 다수), 열이 많아 글자 한 단계 작게는 열 수로(hwpx_fill.DENSE_COLS)
    "stats": {"align": {"label": "CENTER"}},
    # 역할표 — 성명·소속 같은 짧은 칸 가운데, 맡은 일은 왼쪽
    "roles": {},
    # 비교표 — 항목 열 라벨 음영, 맞선 열은 같은 폭
    "compare": {"label_fill": hs.HOUSE["label_fill"], "align": {"label": "LEFT"}},
    # 목록형 — 번호 열 가운데·좁게, 긴 글 왼쪽
    "list": {"col_mm": {"number": 10.0, "label": 22.0}},
    "general": {},
}
# 기본값에서 첫 열을 라벨 칸(음영)으로 두는 것은 요약·비교표뿐 — 머리줄 있는 자료 표의 첫 열은 값이다(캔 값이 음영을 말하면 그것)
LABEL_KINDS = ("summary", "compare")


def merge(base: dict, over: dict | None) -> dict:
    """디자인 값 겹치기 — align·col_mm 은 키마다, 나머지는 통째로. over 의 None 은 덮지 않는다."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update({a: b for a, b in v.items() if b not in (None, "")})
        else:
            out[k] = v
    return out


def default_design(kind: str) -> dict:
    return merge(BASE, KIND_DEFAULT.get(kind) or {})


def column_weights(rows: list[list[str]], design: dict, f: dict | None = None) -> list[float]:
    """열 폭 바람 비율 — 열 값 꼴마다 바라는 폭(design col_mm)에, 글 갈래 열은 가장 긴 값 길이를 곱해(제곱근) 늘린다.
    하한(머리 낱말·짧은 값이 한 줄에)과 상한은 그리는 쪽(house_style.fit_widths·hwpx_fill._fit_widths)이 지킨다."""
    f = f or features(rows)
    rows = _norm(rows)
    mm = design.get("col_mm") or _BASE_MM
    out = []
    for c, t in enumerate(f["types"]):
        w = float(mm.get(t) or _BASE_MM.get(t) or 20.0)
        if t in ("text", "long", "label"):
            longest = max([hs.text_units(r[c]) for r in rows[f["head_rows"]:]] + [1.0])
            ref = {"label": 6.0, "text": 18.0, "long": 40.0}[t]
            w *= max(0.6, min((longest / ref) ** 0.5, 1.8))
        out.append(round(w, 2))
    return out
