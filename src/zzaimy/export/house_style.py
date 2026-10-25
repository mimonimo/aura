"""학교 문서 디자인의 기준값 한 곳 — 한글 작업본(app.webhwp_style·webhwp_templates)과 구글 독스 작업본(ingest.gdocs)이 같은 값을 읽는다.

표 값은 학교 서식·문서 1,459건의 표 20,441개에서 캔 것이다. 머리줄·라벨 칸은 옅은 회색 음영(#E5E5E5)·가운데 정렬,
머리줄 글자는 굵게, 선은 바깥 0.4mm·안쪽 0.12mm 실선, 글자 10pt, 칸 여백 좌우 510·위아래 141(HWPUNIT), 행 최소 높이 1,900.
한 칸 상자(【작성방법】·※ 안내)는 안쪽 굵기 점선, 1pt 작은 회색 글. 도식은 표 칸 상자와 화살표 칸으로 그린다.

글자 체계(TYPE)는 운영 문서함의 학교 서식 3,774건(문단 약 13만 개, 표 칸 글 약 129만 개)의 글자·문단 모양 분포에서
갈래마다 가장 많이 쓴 값을 골랐다(값을 옮기지 않고 분포만 셈 — 근거는 TYPE_BASIS). 갈래는 글 꼴로 나눴다:
표지(문서 앞 8문단 안의 16pt 이상 글), Ⅰ.(장), 1.(1단계), 가.(2단계), 1)(3단계), 본문(○·- 줄이나 40자 넘는 글), 표 칸.
문단 위아래 간격은 학교 서식 대부분이 0(빈 줄로 띄움)이라 0을 기본으로 둔다. 장식은 장·1단계 제목 글자를 남색으로 하는 데서 그친다.

한글 쪽은 HWPUNIT·'0.4 mm' 글을 그대로 쓰고, 독스 쪽은 아래 변환(pt·rgb 0..1)을 거친다.
"""
from __future__ import annotations

import re

HOUSE = {"head_fill": "#E5E5E5", "label_fill": "#E5E5E5", "outer": "0.4 mm", "inner": "0.12 mm",
         "head_bottom": "SOLID", "font_pt": 10.0, "head_bold": True, "head_align": "CENTER", "body_align": ""}
CELL_MARGIN = {"left": 510, "right": 510, "top": 141, "bottom": 141}   # HWPUNIT — 좌우 약 1.8mm, 위아래 약 0.5mm
MIN_ROW_HEIGHT = 1900                                                   # HWPUNIT — 10pt 한 줄 + 여백(약 6.7mm)
BOX = {"dash": "DASH", "font_delta": -1.0, "color": "#555555", "align": "LEFT"}   # 한 칸 안내 상자

# 도식 — 첫 줄(상위·출발) 상자는 연파랑, 나머지는 연회색. 상자 칸:화살표 칸 폭 = 4:1
DIAGRAM_FILLS = ("#DFEAF5", "#F2F2F2")
DIAGRAM_BORDER = "0.12 mm"
DIAGRAM_WEIGHTS = (4, 1)
DIAGRAM_BOX_HEIGHT = 1800                                               # HWPUNIT
DIAGRAM_ARROW_HEIGHT = 900
DIAGRAM_MARGIN = 283                                                    # HWPUNIT(약 1mm) 사방
DIAGRAM_MAX_LINES = 12
DIAGRAM_FENCE = re.compile(r"^```\s*(도식|diagram|flow)")

# ── 색 ── 남색·회색 두 계열만(본문 글은 검정). 남색은 장·1단계 제목 글자와 일정표 칸(연남색), 회색은 표 음영·안내 글
PALETTE = {"accent": "#1F3864",          # 남색 — 장·1단계 제목 글자
           "accent_soft": DIAGRAM_FILLS[0],   # 연남색 — 일정표의 추진 기간 칸, 도식 첫 줄 상자
           "fill": "#E5E5E5",            # 회색 — 머리줄·라벨 칸 음영(표 채굴 최빈값)
           "fill_soft": "#F2F2F2",       # 연회색 — 합계 줄, 줄무늬(기본 끔)
           "muted": "#555555"}           # 진회색 — 작성방법 상자 글

# ── 글자 체계 ── pt 크기, bold 굵게, spacing 자간(%), line 줄 간격(%), before·after 문단 위아래 간격(pt), align 정렬,
# color 글자 색, rule 아래 선 굵기(mm, 빈 값이면 없음 — 기안기 웹 보기가 문단 테두리를 다음 문단 자리에 그려 지금은 쓰지 않는다).
# 표 칸 글(table)·작성방법 상자(box)도 같은 표에 둔다
TYPE = {
    "cover_title": {"pt": 20, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "CENTER"},
    "cover_sub":   {"pt": 14, "bold": False, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "CENTER"},
    "cover_date":  {"pt": 12, "bold": False, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "CENTER"},
    "cover_org":   {"pt": 15, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "CENTER"},
    "toc_title":   {"pt": 15, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 6, "align": "CENTER"},
    "toc":         {"pt": 11, "bold": False, "spacing": 0, "line": 180, "before": 0, "after": 0, "align": "JUSTIFY"},
    "chapter":     {"pt": 15, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "JUSTIFY", "color": "#1F3864"},
    "h1":          {"pt": 12, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "JUSTIFY", "color": "#1F3864"},
    "h2":          {"pt": 11, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "JUSTIFY"},
    "h3":          {"pt": 11, "bold": False, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "JUSTIFY"},
    "body":        {"pt": 11, "bold": False, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "JUSTIFY"},
    "table":       {"pt": 10, "bold": False, "spacing": 0, "line": 130, "before": 0, "after": 0, "align": ""},
    "table_head":  {"pt": 10, "bold": True, "spacing": 0, "line": 130, "before": 0, "after": 0, "align": "CENTER"},
    "box":         {"pt": 9, "bold": False, "spacing": 0, "line": 130, "before": 0, "after": 0, "align": "LEFT", "color": "#555555"},
    "sign":        {"pt": 12, "bold": False, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "CENTER"},
    "sign_org":    {"pt": 14, "bold": True, "spacing": 0, "line": 160, "before": 0, "after": 0, "align": "RIGHT"},
}
# 근거 — 운영 문서함 학교 서식 3,774건에서 갈래별로 센 분포(상위 값, 괄호는 문단 수). 고른 값과 다른 경우는 까닭을 적었다
TYPE_BASIS = {
    "cover_title": "크기 20(544)·24(475)·18(311), 굵게 58%, 가운데 89%, 줄 간격 160(60%)",
    "cover_sub": "부제는 따로 세지 않았다 — 표지 제목(20)과 본문(11) 사이 단계로 14",
    "cover_date": "날짜·기관은 따로 세지 않았다 — 부제보다 한 단계 작게 12, 기관은 장 제목과 같은 15 굵게",
    "chapter": "Ⅰ. 꼴 크기 15(489)·14(423), 굵게 73%, 줄 간격 100(55%)·160(22%) — 줄 간격은 본문과 맞춰 160",
    "h1": "1. 꼴 크기 10(4,386)·12(2,177)·13(1,023), 굵게 70% — 10은 본문 이하라 단계가 안 보여 다음 값 12",
    "h2": "가. 꼴 크기 11(486)·12(357)·10(267), 굵게 71%, 줄 간격 160(65%)",
    "h3": "1) 꼴 크기 11(1,386)·12(717), 굵게 35%(보통이 많음), 줄 간격 160(56%)·140(32%)",
    "body": "본문 크기 11(14,096)·10(13,827)·12(7,547), 양쪽 정렬 85%, 자간 0(78%), 줄 간격 160(48%), 문단 위아래 간격 0(91%)",
    "table": "표 칸 글 크기 10(62%)·11(15%), 가운데 70%, 자간 0(70%)·-3(13%), 줄 간격 160(43%)·130(29%) — 칸이 덜 벌어지게 130",
    "box": "표 칸 글 크기 분포에서 9pt(10%) — 본문 표보다 한 단계 작게, 진회색",
    "spacing": "자간은 모든 갈래에서 0 이 가장 많다(표지 69%, 본문 78%)",
    "sign": "제출 문구(「위와 같이 … 제출합니다」·날짜)는 따로 세지 않았다 — 독스 판과 같이 가운데, 본문보다 한 단계 큰 12. 기관장 줄은 오른쪽 14 굵게",
}

# ── 표 디자인 ── 선 굵기 위계(바깥 0.4 > 머리줄 아래 0.25 > 안쪽 0.12), 머리줄·라벨 음영, 합계 줄 강조, 줄무늬 기본 끔
TABLE = {
    "outer": "0.4 mm", "inner": "0.12 mm",
    "head_rule": "SOLID",                # 머리줄 아래 선 — SOLID(굵게)·DOUBLE(이중선)·THIN(안쪽 선과 같게)
    "head_rule_width": "0.25 mm",
    "total_fill": PALETTE["fill_soft"], "total_bold": True, "total_rule_width": "0.25 mm",   # 합계 줄 — 연회색·굵게·위 선 굵게
    "zebra": False, "zebra_fill": "#F7F7F7",
    "num_align": "RIGHT",                # 금액·큰 수 칸
    "col_min_mm": 12.0,                  # 열 폭 하한(mm) — 머리 낱말이 한 줄에 들 폭이 이보다 크면 그 폭
    "col_max_share": 0.55,               # 한 열이 표 폭에서 차지하는 상한
    "month_min_mm": 6.0,                 # 일정표(간트) 달 칸 하한(mm) — 12mm 하한이면 달 12칸이 표 폭을 거의 다 먹는다
    "month_fill": PALETTE["accent_soft"],  # 일정표 추진 기간 칸
}
# ── 쪽 모양 ── 공통 양식의 용지·여백(mm) 한 곳. 학교 실물 표본은 좌우 20mm 가 대다수이고 독스 판(gdocs_templates)도 20mm 다.
# 한글 새 문서 기본값(좌우 30mm)을 쓰던 것을 바꿨다(10-25). 본문 폭(TEXT_W)은 여기서 셈한다
PAGE = {"paper_mm": (210.0, 297.0), "margin_mm": {"top": 20.0, "bottom": 15.0, "left": 20.0, "right": 20.0, "header": 15.0, "footer": 15.0},
        "page_number": "BOTTOM_CENTER", "page_number_min_heads": 8}     # 제목이 이만큼 넘는 여러 쪽 문서에 쪽 번호
HU_PER_MM = 7200 / 25.4


def text_width() -> int:
    """본문 폭(HWPUNIT) — 용지 폭에서 좌우 여백을 뺀 값(한글이 셈하는 것과 같게 HWPUNIT 로 바꾼 뒤 뺀다)."""
    m = page_margins_hu()
    return int(round(PAGE["paper_mm"][0] * HU_PER_MM)) - m["left"] - m["right"]


def page_margins_hu() -> dict[str, int]:
    return {k: int(round(v * HU_PER_MM)) for k, v in PAGE["margin_mm"].items()}


TOTAL_LABEL = re.compile(r"^\s*(합\s*계|총\s*계|소\s*계|계)\s*$")
MONEY_HEAD = re.compile(r"금액|예산|단가|집행액|사업비|비용|\((?:천|백만|억)?원\)|단위\s*:\s*(?:천|백만|억)?원")
_AMOUNT = re.compile(r"[-+△▲▽]?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+△▲▽]?\s*\d{4,}(?:\.\d+)?")


def short_value(t: str) -> bool:
    """가운데에 둘 값 — 기호(●○◎-), 숫자·금액·비율·날짜, 여섯 글자 이하."""
    t = (t or "").strip()
    return bool(t) and (len(t) <= 6 or bool(re.fullmatch(r"[\d\s,.%~\-–()/:·년월일천백만억원명개건회점]+", t)))


def heading_type(text: str, level: int, chapter_mode: bool = False) -> str:
    """제목 글과 단계 → TYPE 갈래. 번호 꼴이 먼저(Ⅰ.→장, 1.→1단계, 가.→2단계, 1)→3단계), 번호가 없으면 단계로
    (chapter_mode 는 그 문서의 1단계가 Ⅰ. 꼴 장일 때 — 번호 없는 1단계도 장 크기)."""
    t = (text or "").strip()
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?\s", t):
        return "chapter"
    if re.match(r"^\d{1,2}\.\s", t):
        return "h1"
    if re.match(r"^[가-하]\.\s", t):
        return "h2"
    if re.match(r"^(\d{1,2}|[가-하])\)\s", t):
        return "h3"
    if level <= 1:
        return "chapter" if chapter_mode else "h1"
    return {2: "h1" if chapter_mode else "h2", 3: "h2" if chapter_mode else "h3"}.get(level, "h3")


def is_total(text: str) -> bool:
    """합계 줄의 첫 칸 글(합계·총계·소계·계)."""
    return bool(TOTAL_LABEL.match(text or ""))


def is_amount(text: str) -> bool:
    """금액·큰 수(천 단위 쉼표가 있거나 네 자리 이상) — 오른쪽 정렬할 값."""
    return bool(_AMOUNT.fullmatch((text or "").strip()))


def money_head(head: str) -> bool:
    return bool(MONEY_HEAD.search(head or ""))


def thousands(text: str) -> str:
    """쉼표 없는 네 자리 이상 정수·소수에 천 단위 쉼표 — 값을 바꾸지 않고 꼴만(계산 없음). 다른 글은 그대로."""
    t = (text or "").strip()
    m = re.fullmatch(r"([-+△▲▽]?)(\d{4,})(\.\d+)?", t)
    if not m:
        return text
    return f"{m.group(1)}{int(m.group(2)):,}{m.group(3) or ''}"


def value_align(text: str, head: str = "") -> str:
    """값 칸 정렬 — 금액 칸(머리가 금액·원 단위)이나 큰 수는 오른쪽, 기호·짧은 값은 가운데, 나머지 글은 비움(문단 기본, 양쪽)."""
    t = (text or "").strip()
    if not t:
        return ""
    if is_amount(t) or (money_head(head) and re.fullmatch(r"[-+△▲▽]?[\d,.]+", t)):
        return TABLE["num_align"]
    if short_value(t):
        return "CENTER"
    return ""


def text_units(text: str) -> float:
    """글 폭 어림(한글 한 글자 = 1) — 한글·전각은 1, 반각은 0.55."""
    return sum(1.0 if ord(c) > 0x2E7F or c == "·" else 0.55 for c in str(text or ""))


def fit_widths(columns: list[list[str]], total: int, weights: list[float] | None = None, *, pt: float = 10.0,
               pad: int = 1020, min_w: int | None = None, max_share: float | None = None,
               spans: list[list[int]] | None = None) -> list[int]:
    """열 폭(HWPUNIT, 합 = total) — 열마다 글 묶음(머리 + 값들)을 보고 정한다.
    하한: 머리의 가장 긴 낱말이 한 줄에 드는 폭(글자 폭 + 좌우 칸 여백 pad)과 col_min_mm 가운데 큰 값.
    바람: weights(양식 정의의 비율)가 있으면 그 비율, 없으면 칸 글 길이(긴 값은 20글자에서 자름)의 제곱근 — 긴 글 칸이 넓되 한 열이 다 먹지 않게.
    하한에는 8글자 안쪽 짧은 칸 글도 센다(짧은 값·안내 글이 두 줄로 꺾이지 않게).
    상한: 표 폭 × col_max_share. 하한 합이 표 폭을 넘으면 하한 비율로 줄인다. 반올림 나머지는 가장 넓은 열에.
    spans — columns 와 같은 꼴의 칸 열 수(합친 칸은 2 이상). 합친 칸 글은 덮은 열 수로 나눠 센다(한 열에 몰아 세면 그 열만 넓어진다)."""
    n = len(columns)
    if n == 0 or total <= 0:
        return []
    char = pt * 100                                      # 한 글자 폭(HWPUNIT) ≈ 글자 크기
    floor = int(min_w if min_w is not None else TABLE["col_min_mm"] * 283.465)
    cap = int(total * (max_share if max_share is not None else TABLE["col_max_share"])) if n >= 3 else total   # 2열(라벨·값)은 값 칸이 넓다
    def span(i: int, k: int) -> int:
        try:
            return max(1, int(spans[i][k])) if spans else 1
        except (IndexError, TypeError, ValueError):
            return 1
    need = []
    for i, col in enumerate(columns):
        head = str(col[0] if col else "")
        words = head.split() or [""]
        s0 = span(i, 0)
        # 합친 칸은 덮은 열마다 자기 몫(글 폭 ÷ 열 수)만 — 하한만 늘리고 비율은 그대로
        units = [text_units(w) / s0 for w in words]
        units += [text_units(x) / span(i, k) for k, x in enumerate(col) if 0 < text_units(x) <= 8]   # 짧은 칸 글(8글자 안쪽)은 한 줄에 들게
        unit = max(units)
        need.append(max(floor, int(unit * char * 1.15) + pad))
    if sum(need) >= total:
        out = [int(total * x / sum(need)) for x in need]
    else:
        want = []
        for i, col in enumerate(columns):
            longest = max([min(text_units(x) / span(i, k), 20.0) for k, x in enumerate(col)] + [1.0])
            # 비율을 주면(양식 정의) 그 비율이 바람이고 글 길이는 하한(need)으로만, 없으면 글 길이의 제곱근
            w = float(weights[i]) if weights and i < len(weights) and float(weights[i]) > 0 else longest ** 0.5
            want.append(w)
        out = [0] * n
        fixed: dict[int, int] = {}
        for _ in range(n + 1):                           # 하한·상한에 걸린 열은 묶고 나머지를 다시 나눈다
            rest = total - sum(fixed.values())
            free = [i for i in range(n) if i not in fixed]
            sw = sum(want[i] for i in free) or 1.0
            changed = False
            for i in free:
                v = int(rest * want[i] / sw)
                if v < need[i]:
                    fixed[i], changed = need[i], True
                elif v > cap and len(free) > 1:
                    fixed[i], changed = cap, True
            if not changed:
                for i in free:
                    fixed[i] = int(rest * want[i] / sw)
                break
        out = [fixed[i] for i in range(n)]
    out[out.index(max(out))] += total - sum(out)
    return out


# ── 표 갈래 ── 27B 가 고른 갈래와 내용(라벨·과제·비목)을 받아 학교 문서다운 격자로 짠다. 값은 받은 그대로(금액은 천 단위 쉼표 꼴만, 합계를 셈하지 않음).
# 돌려주는 것: {rows: 칸 글 격자(머리줄 포함), head_rows, weights: 열 폭 비율, merges: [(행, 열, 행 수, 열 수)], marks: [(행, 열)] 음영 칸,
# min_w: 열 폭 하한(HWPUNIT, 없으면 기본)}
def summary_grid(pairs, cols: int = 2) -> dict:
    """요약 표 — 라벨·값 2열 또는 4열(라벨|값|라벨|값). pairs = [[라벨, 값]…] 또는 [라벨…](값 빈칸). 4열에서 홀수면 마지막 값 칸을 끝까지 합친다."""
    items = [(str(p[0]), str(p[1]) if len(p) > 1 else "") if isinstance(p, (list, tuple)) else (str(p), "") for p in (pairs or [])]
    items = [(a.strip(), b) for a, b in items if a.strip()]
    if not items:
        raise ValueError("요약 표에 라벨이 하나도 없다")
    if cols not in (2, 4):
        raise ValueError("요약 표는 2열 또는 4열이다")
    rows, merges = [], []
    if cols == 2:
        rows = [[a, b] for a, b in items]
        weights = [1, 3]
    else:
        for i in range(0, len(items), 2):
            a = items[i]
            b = items[i + 1] if i + 1 < len(items) else None
            if b:
                rows.append([a[0], a[1], b[0], b[1]])
            else:
                rows.append([a[0], a[1], "", ""])
                merges.append((len(rows) - 1, 1, 1, 3))
        weights = [1, 2, 1, 2]
    return {"rows": rows, "head_rows": 0, "weights": weights, "merges": merges, "marks": [], "min_w": None}


def schedule_grid(tasks, months, marks: dict | None = None, head: str = "추진 과제") -> dict:
    """일정표(간트 꼴) — 머리줄 [추진 과제, 월…], 과제마다 한 줄, 추진하는 달 칸은 연남색 음영(marks = {과제: [달…]}, 달은 months 의 글이나 1부터 번호)."""
    tasks = [str(t).strip() for t in (tasks or []) if str(t).strip()]
    months = [str(m).strip() for m in (months or []) if str(m).strip()]
    if not tasks or not months:
        raise ValueError("일정표에는 과제와 달이 하나 이상 있어야 한다")
    if len(months) > 24:
        raise ValueError("일정표의 달 칸은 24개까지다")
    rows = [[head, *months]] + [[t] + [""] * len(months) for t in tasks]
    out_marks = []
    for ti, t in enumerate(tasks):
        for m in (marks or {}).get(t, []):
            m = str(m).strip()
            if m in months:
                ci = months.index(m) + 1
            elif m.isdigit() and 1 <= int(m) <= len(months):
                ci = int(m)
            else:
                continue
            out_marks.append((ti + 1, ci))
    return {"rows": rows, "head_rows": 1, "weights": [4] + [1] * len(months), "merges": [], "marks": out_marks,
            "min_w": int(TABLE["month_min_mm"] * 283.465)}


def budget_grid(items, unit: str = "천원", total: str | None = None) -> dict:
    """예산표 — 머리줄 [비목, 금액(단위), 비고], 비목마다 한 줄, 끝에 합계 줄. 금액은 받은 값에 천 단위 쉼표 꼴만 입히고
    합계는 total 로 받은 값(없으면 빈칸) — 계산하지 않는다(예산 계산은 담당자·예산 문서 값)."""
    rows = [["비목", f"금액({unit})" if unit else "금액", "비고"]]
    for it in items or []:
        it = list(it) if isinstance(it, (list, tuple)) else [it]
        name = str(it[0]).strip() if it else ""
        if not name:
            continue
        rows.append([name, thousands(str(it[1])) if len(it) > 1 and it[1] is not None else "", str(it[2]) if len(it) > 2 and it[2] else ""])
    if len(rows) == 1:
        raise ValueError("예산표에 비목이 하나도 없다")
    rows.append(["합계", thousands(str(total)) if total not in (None, "") else "", ""])
    return {"rows": rows, "head_rows": 1, "weights": [2, 1.5, 2], "merges": [], "marks": [], "min_w": None}


def cell_roles(cells: list[dict]) -> dict[tuple[int, int], str]:
    """칸 목록({r, c, rs, cs, text, field}) → (행, 열) → 'head'·'label'·'body'·'box'.
    머리줄=첫 행이 모두 글이고 아래 행이 있을 때, 라벨=글 있는 칸 바로 오른쪽이 빈 칸·필드."""
    if not cells:
        return {}
    nrows = max(c["r"] + c["rs"] for c in cells)
    ncols = max(c["c"] + c["cs"] for c in cells)
    if ncols == 1:
        return {(c["r"], c["c"]): "box" for c in cells}
    at = {(c["r"], c["c"]): c for c in cells}
    first = [c for c in cells if c["r"] == 0]
    head_rows = 0
    if nrows >= 2 and first and all(c["text"] and not c["field"] for c in first) and len(first) >= 2:
        head_rows = max(c["rs"] for c in first)
        # 머리줄이 두 줄(합친 칸 아래 세부 머리)인 표 — 둘째 줄도 모두 글이고 그 아래 빈 줄이 있으면
        second = [c for c in cells if c["r"] == head_rows]
        if (head_rows == 1 and nrows >= 3 and second and all(c["text"] and not c["field"] for c in second)
                and any(c["cs"] > 1 for c in first) and len(second) < ncols):
            head_rows = 2
    out = {}
    for c in cells:
        if c["r"] < head_rows:
            out[(c["r"], c["c"])] = "head"
            continue
        right = at.get((c["r"], c["c"] + c["cs"]))
        below = at.get((c["r"] + c["rs"], c["c"]))
        numeric = bool(re.fullmatch(r"[-+△▲▽]?[\d,.]+\s*%?", c["text"] or ""))      # 수·금액 칸은 라벨이 아니다(빈 비고 칸 옆 값)
        is_label = bool(c["text"]) and not c["field"] and not numeric and len(c["text"]) <= 20 and (
            (right is not None and (not right["text"] or right["field"]))
            or (c["c"] == 0 and not head_rows and ncols >= 2)
            # 표 안 소머리(아래 칸이 비었거나 필드이고 오른쪽도 글인 칸 — 「국비 | 지방비 | 대응자금」 줄)
            or (below is not None and (not below["text"] or below["field"]) and c["c"] > 0))
        out[(c["r"], c["c"])] = "label" if is_label else "body"
    return out


def grid_cells(rows: list[list[str]]) -> list[dict]:
    """글 격자(병합 없음) → cell_roles 가 받는 칸 목록."""
    n = max((len(r) for r in rows), default=0)
    return [{"r": ri, "c": ci, "rs": 1, "cs": 1, "field": False,
             "text": " ".join(str(r[ci]).split()) if ci < len(r) else ""}
            for ri, r in enumerate(rows) for ci in range(n)]


def diagram_lines(text: str) -> list[list[str]]:
    """```도식 블록 글 → 상자 줄 목록. 줄마다 「상자 → 상자」, 화살표만 있는 줄(↓)은 건너뛴다."""
    out = []
    for ln in str(text or "").split("\n"):
        boxes = [b.strip() for b in re.split(r"\s*(?:→|->|⇒|=>)\s*", ln.strip()) if b.strip()]
        if boxes and not re.fullmatch(r"[↓|]+", ln.strip()):
            out.append(boxes)
    return out[:DIAGRAM_MAX_LINES]


def diagram_grid(lines: list[list[str]]) -> dict:
    """상자 줄 → 도식 표의 격자. 열 = 상자 칸·화살표 칸 번갈아(2n-1), 줄 사이에 아래 화살표 행.
    rows 는 행마다 칸 목록 [{c, span, text, kind: box|arrow|blank, bold}], kind 가 box 인 칸만 선·음영이 있다."""
    n = max((len(b) for b in lines), default=1)
    cols = max(2 * n - 1, 1)
    rows: list[dict] = []
    for i, boxes in enumerate(lines):
        if i:
            rows.append({"kind": "arrow", "height": DIAGRAM_ARROW_HEIGHT,
                         "cells": [{"c": 0, "span": cols, "text": "↓", "kind": "arrow", "bold": False}]})
        fill = DIAGRAM_FILLS[0] if i == 0 else DIAGRAM_FILLS[1]
        cells: list[dict] = []
        if len(boxes) == 1:
            cells.append({"c": 0, "span": cols, "text": boxes[0], "kind": "box", "bold": True, "fill": fill})
        else:
            k = len(boxes)
            lead = n - k                                     # 상자 k 개를 가운데에 — 양옆 빈 칸(선 없음)
            c = 0
            if lead:
                cells.append({"c": 0, "span": lead, "text": "", "kind": "blank", "bold": False})
                c = lead
            for j, b in enumerate(boxes):
                cells.append({"c": c, "span": 1, "text": b, "kind": "box", "bold": i == 0, "fill": fill})
                c += 1
                if j < k - 1:
                    cells.append({"c": c, "span": 1, "text": "→", "kind": "arrow", "bold": False})
                    c += 1
            if c < cols:
                cells.append({"c": c, "span": cols - c, "text": "", "kind": "blank", "bold": False})
        rows.append({"kind": "boxes", "height": DIAGRAM_BOX_HEIGHT, "cells": cells})
    weights = [DIAGRAM_WEIGHTS[0] if c % 2 == 0 else DIAGRAM_WEIGHTS[1] for c in range(cols)]
    return {"cols": cols, "rows": rows, "weights": weights}


def split_diagram_blocks(text: str) -> list[tuple[str, str]]:
    """글 → [("text"|"diagram", 글)] — ```도식 … ``` 블록만 떼어 낸다(닫는 ``` 가 없으면 끝까지 도식)."""
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    fence: list[str] | None = None
    for ln in str(text or "").replace("\r", "").split("\n"):
        s = ln.strip()
        if fence is not None:
            if s.startswith("```"):
                out.append(("diagram", "\n".join(fence)))
                fence = None
            else:
                fence.append(ln)
            continue
        if DIAGRAM_FENCE.match(s):
            if "\n".join(buf).strip():
                out.append(("text", "\n".join(buf).strip("\n")))
            buf, fence = [], []
            continue
        buf.append(ln)
    if fence:
        out.append(("diagram", "\n".join(fence)))
    if "\n".join(buf).strip():
        out.append(("text", "\n".join(buf).strip("\n")))
    return out


# ── 독스 단위 변환 ── 독스 API 는 길이를 pt, 색을 rgb 0..1 로 받는다
def mm(w: str | float) -> float:
    """'0.4 mm'·0.4 → 0.4"""
    try:
        return float(re.sub(r"[^\d.]", "", str(w)) or 0)
    except ValueError:
        return 0.0


def mm_to_pt(w: str | float) -> float:
    return round(mm(w) * 72 / 25.4, 3)


def hwpunit_to_pt(v: int | float) -> float:
    """HWPUNIT(1/7200 인치) → pt(1/72 인치) — 100 으로 나눈다."""
    return round(float(v) / 100, 3)


def rgb(hex_color: str) -> dict | None:
    """'#E5E5E5' → {'red': 0.898, …}. 'none'·빈 값이면 None."""
    m = re.fullmatch(r"#?([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})", str(hex_color or "").strip())
    if not m:
        return None
    r, g, b = (round(int(x, 16) / 255, 3) for x in m.groups())
    return {"red": r, "green": g, "blue": b}


def docs_house(opts: dict | None = None) -> dict:
    """독스 표 서식 값(pt·rgb) — HOUSE(와 덮을 값)에서 바꿔 만든다."""
    o = {**HOUSE, **(opts or {})}
    return {"head_rgb": rgb(o["head_fill"]), "label_rgb": rgb(o["label_fill"]),
            "outer_pt": mm_to_pt(o["outer"]), "inner_pt": mm_to_pt(o["inner"]),
            "font_pt": float(o["font_pt"]), "box_font_pt": float(o["font_pt"]) + BOX["font_delta"],
            "box_rgb": rgb(BOX["color"]), "box_dash": BOX["dash"], "box_align": BOX["align"],
            "head_bold": bool(o["head_bold"]), "head_align": o["head_align"], "body_align": o.get("body_align") or "",
            "padding": {k: hwpunit_to_pt(v) for k, v in CELL_MARGIN.items()},
            "min_row_pt": hwpunit_to_pt(MIN_ROW_HEIGHT),
            "diagram_border_pt": mm_to_pt(DIAGRAM_BORDER), "diagram_fills": [rgb(f) for f in DIAGRAM_FILLS],
            "diagram_padding_pt": hwpunit_to_pt(DIAGRAM_MARGIN),
            "diagram_box_pt": hwpunit_to_pt(DIAGRAM_BOX_HEIGHT), "diagram_arrow_pt": hwpunit_to_pt(DIAGRAM_ARROW_HEIGHT),
            "head_rule": str(o.get("head_bottom") or TABLE["head_rule"]).upper(), "head_rule_pt": mm_to_pt(TABLE["head_rule_width"]),
            "total_rgb": rgb(TABLE["total_fill"]), "total_bold": bool(TABLE["total_bold"]), "total_rule_pt": mm_to_pt(TABLE["total_rule_width"]),
            "zebra": bool(o.get("zebra", TABLE["zebra"])), "zebra_rgb": rgb(TABLE["zebra_fill"]),
            "line_pct": TYPE["table"]["line"]}


def docs_type(kind: str) -> dict:
    """글자 체계 한 갈래를 독스 값으로 — {pt, bold, line(%), above·below(pt), align(START·CENTER·END·JUSTIFIED), rgb, rule_pt, rule_rgb}."""
    st = TYPE[kind]
    return {"pt": float(st["pt"]), "bold": bool(st.get("bold")), "line": int(st.get("line") or 100),
            "above": float(st.get("before") or 0), "below": float(st.get("after") or 0),
            "align": {"CENTER": "CENTER", "LEFT": "START", "RIGHT": "END", "JUSTIFY": "JUSTIFIED"}.get(st.get("align") or "", ""),
            "rgb": rgb(st.get("color") or ""), "rule_pt": mm_to_pt(st["rule"]) if st.get("rule") else 0.0,
            "rule_rgb": rgb(PALETTE["accent"])}
