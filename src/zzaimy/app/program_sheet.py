"""사업 현황 엑셀 → 사업 관리 표의 연도별 값(ADR-0066).

학교가 해마다 내는 재정지원사업 현황 표처럼 「한 줄 = 그해 진행한 사업(세부 과제) 하나」인 엑셀을 읽어 사업 관리 표(program_admin)의
줄과 잇고, 줄마다 연도별 값(예산 6갈래·책임자·행정 담당자·부서·기간 …)과 세부 과제 줄을 채운다. 장부(kg_external.json)와 그래프는
건드리지 않는다.

- 파일 꼴(load_sheets): xlsx·xlsm 은 openpyxl, 옛 xls(OLE)는 xlrd 가 있으면 그것으로, 엑셀이 「웹 페이지」로 저장한 html·mht 와
  XML 스프레드시트(2003)는 표 태그를 직접 읽는다. 확장자가 아니라 파일 앞머리로 가린다(xlsx 이름의 html 도 읽힌다).
- 읽기(read_sheets): 머리줄은 「사업명」 칸이 있는 줄. 열은 머리줄 이름의 낱말로 맞추므로 해마다 열 순서·개수가 달라도 된다
  (구분·회계연도·회계처리부서·인센티브처럼 어떤 해에만 있는 열도 그대로). 병합 칸은 왼쪽 위 값으로 채우되(가로로 합친 머리줄
  「총사업기간」 = 시작·끝 두 칸) 금액 칸은 겹셈을 막으려 왼쪽 위에서만 읽는다. 빈 줄·메모 줄은 건너뛰고, 맨 아래 합계 줄은
  대조용으로 따로 읽는다(meta.total). 연도는 제목의 「NNNN학년도·년도」, 없으면 시트 이름·문서 이름의 연도.
  위쪽 안내 줄의 「기준일」「사업선정 대상기간」, 2단 머리줄의 묶음 이름, 원본 머리줄 글과 열 순서도 meta 로 돌려준다(내려받기에 쓴다).
- 잇기(match): 이름 열쇠(program_names.group_key)가 줄의 이름·별칭 열쇠와 같으면 그 줄, 괄호 속 사업 이름이 같으면 그 줄,
  아니면 이름 속에 든 가장 긴 별칭. 가장 긴 겹침이 둘 이상의 줄이면 모호로 남기고 채우지 않는다. 못 찾으면 「엑셀에서 온 줄」로 더한다.
- 채우기(plan): 줄마다 years[연도] 에 값을 적는다 — 세부 과제가 여러 줄이면 금액은 더하고 글은 잇고, 줄 하나하나는 tasks 로 보존한다.
  손으로 고친 칸(by_hand)은 덮지 않는 것이 기본이다. 해마다의 기준일·대상기간·열 순서·합계 줄은 저장값의 sheets[연도] 에 둔다.
- 내려받기(write_book): 연도 하나의 줄을 원본과 같은 2단 머리줄·열 순서·합계 줄·천 단위 쉼표 서식의 xlsx 로.
"""
from __future__ import annotations

import copy
import datetime as _dt
import io
import re
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from zzaimy.app.program_names import group_key
from zzaimy.app.program_register import slug

# 머리줄 낱말(띄어쓰기·줄바꿈을 지운 뒤 비교) — 앞에 적은 열쇠가 먼저 칸을 가져간다. EXACT 는 칸 글 전체가 같아야 한다
EXACT = {"seq": ("순", "번호", "연번", "순번", "no", "No")}
HEAD = (
    ("seq", ()),
    ("fiscal_year", ("회계연도",)),
    ("name", ("사업명",)),
    ("sub", ("세부사업", "과제명")),
    ("year_period", ("당해연도사업기간", "당해연도기간")),
    ("period", ("총사업기간", "사업기간")),
    ("manager", ("사업책임자", "책임자")),
    ("officer", ("행정담당자", "담당자")),
    ("dept", ("업무진행부서", "주관부서", "담당부서", "업무부서")),
    ("account_dept", ("회계처리부서",)),
    ("majors", ("참여학과", "대상학과")),
    ("ministry", ("지원기관",)),
    ("agency", ("주관",)),
    ("status", ("기존", "신규", "종료", "구분")),
    ("national", ("국고",)),
    ("local", ("지자체",)),
    ("company", ("기업체",)),
    ("school", ("교비",)),
    ("other", ("기타(", "교육청")),
    ("incentive", ("인센티브",)),
    ("carry", ("이월",)),
    ("half", ("상반기",)),
)
# 예산 6갈래(원본 열 순서) — 국고·지자체·기업체·교비대응·기타·인센티브, 그리고 그해 합계(budget)
SPLIT = ("national", "local", "company", "school", "other", "incentive")
MONEY = ("budget", *SPLIT, "carry", "budget_all", "half")
TEXTS = ("sub", "ministry", "agency", "manager", "officer", "dept", "account_dept", "majors", "status", "fiscal_year")
# 화면·저장에 쓰는 연도별 칸(사업 단위로 묶은 값)
YEAR_FIELDS = ("budget", "manager", "dept", "period", "year_period", "sub", "ministry", "agency", "status", "majors",
               "account_dept", "officer", "national", "local", "company", "school", "other", "incentive", "carry",
               "budget_all", "half", "fiscal_year")
LABELS = {"budget": "합계", "manager": "사업 책임자", "dept": "업무진행부서", "period": "총사업기간", "year_period": "당해 기간",
          "sub": "세부 과제", "ministry": "지원기관", "agency": "주관기관", "status": "구분", "majors": "참여 학과",
          "account_dept": "회계처리부서", "officer": "행정 담당자", "national": "국고", "local": "지자체", "company": "기업체",
          "school": "교비", "other": "기타", "incentive": "인센티브", "carry": "이월금", "budget_all": "이월 포함 합계",
          "half": "상반기 합계", "fiscal_year": "회계연도", "seq": "순", "name": "사업명"}
# 내려받기 기본 열(원본 열 순서를 모를 때) — 최근 판의 원본 머리줄
DEFAULT_COLUMNS = (
    ("status", "기존,신규,종료"), ("seq", "순"), ("fiscal_year", "회계연도"), ("name", "사업명"), ("sub", "세부 사업(과제)명"),
    ("ministry", "지원기관"), ("agency", "주관(업무)기관"), ("period", "총사업기간"), ("year_period", "당해연도\n사업기간"),
    ("manager", "사업\n책임자"), ("majors", "사업 참여학과\n(대상학과)"), ("officer", "행정\n담당자"),
    ("dept", "기타사항\n(업무진행부서)"), ("account_dept", "회계처리부서"), ("national", "국고(A)"),
    ("local", "지자체(B)\n(대구/경북)"), ("company", "기업체(C)"), ("school", "교비대응(D)"), ("other", "기타(E)\n(교육청 등)"),
    ("incentive", "인센티브(F)"), ("budget", "합계"),
)
# 금액 단위 — 자동(1억 이상 「53.1억」, 1만~1억 「8,190만」, 그 아래 원)·원·천원·백만원·억원. 화면·합계·요약·상세가 같은 함수를 쓴다
UNITS = {"auto": (0, "자동"), "won": (1, "원"), "thousand": (1000, "천원"), "million": (1_000_000, "백만원"), "eok": (10 ** 8, "억원")}

_YEAR_TITLE = re.compile(r"((?:19|20)\d{2})\s*(?:학년도|년도|회계연도|년)")
_YEAR_ANY = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_LEAD_YEAR = re.compile(r"^\s*(?:19|20)\d{2}\s*(?:년도|학년도|년)?\s*(?:상반기|하반기)?\s*")
_TOTAL = re.compile(r"^\s*(?:합\s*계|소\s*계|총\s*계|계|누\s*계)\s*$")
_GRAND = re.compile(r"^\s*(?:합\s*계|총\s*계)\s*$")
_NOTE = ("※", "*", "주)", "비고")
_DATE = re.compile(r"((?:19|20)\d{2})[.\-/년\s]*(\d{1,2})[.\-/월\s]*(\d{1,2})")
_BASE = re.compile(r"((?:19|20)\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2})\s*[.일]?\s*(?:\(\S+\))?\s*기준")
_TARGET = re.compile(r"대상\s*기간\s*[:：]?\s*([^\n▣※]+)")


def _flat(t) -> str:
    return re.sub(r"\s+", "", str(t or ""))


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (_dt.datetime, _dt.date)):
        return f"{v.year}.{v.month:02d}.{v.day:02d}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _head_text(v) -> str:
    """머리줄 글 — 줄바꿈은 살리고 줄마다 띄어쓰기만 정리(내려받기에서 원본처럼 보이게)."""
    return "\n".join(" ".join(ln.split()) for ln in str(v or "").splitlines() if ln.strip())


def _dates(t: str) -> list[str]:
    """글 속 날짜들 → 「YYYY.MM.DD」. 「20220301-20270228」「2022.3.1~2027.2.28」 꼴."""
    out = []
    for m in re.finditer(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)", t):
        out.append((m.start(), f"{m.group(1)}.{m.group(2)}.{m.group(3)}"))
    if not out:
        for m in _DATE.finditer(t):
            mo, d = int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.append((m.start(), f"{m.group(1)}.{mo:02d}.{d:02d}"))
    return [d for _, d in sorted(out)]


def fmt_period(parts: list) -> str:
    """기간 칸(하나 또는 시작·끝 두 칸) → 「2022.03.01~2027.02.28」. 날짜를 못 읽으면 글 그대로."""
    texts = [_text(p) for p in parts if _text(p)]
    ds = [d for t in texts for d in _dates(t)]
    if ds:
        return ds[0] if len(ds) == 1 else f"{ds[0]}~{ds[-1]}"
    return " ~ ".join(texts)


def money(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    t = re.sub(r"[,\s원]", "", str(v))
    if t in ("-", "–", "—"):
        return 0
    return int(t) if re.fullmatch(r"-?\d+", t) else None


_KUNIT = {"조": 10 ** 12, "억": 10 ** 8, "천만": 10 ** 7, "백만": 10 ** 6, "만": 10 ** 4, "천": 10 ** 3}


def parse_won(v) -> int | None:
    """금액 글 → 원. 「1,200원」「9억 원」「12억 3,000만 원」「3.5억」. 못 읽으면 None(손으로 적은 글 칸의 합계용)."""
    n = money(v)
    if n is not None:
        return n
    s = re.sub(r"[\s,원]", "", str(v or ""))
    if not s:
        return None
    total, pos = 0.0, 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)(조|억|천만|백만|만|천)?", s):
        if m.start() != pos:
            return None
        total += float(m.group(1)) * _KUNIT.get(m.group(2) or "", 1)
        pos = m.end()
    return int(round(total)) if pos == len(s) and pos else None


def won(n: int | None) -> str:
    return "" if n is None else f"{n:,}원"


def _one(x: float) -> str:
    """소수 한 자리, 끝 0 빼기 — 53.1 · 53 · 1,234.5."""
    t = f"{x:,.1f}"
    return t[:-2] if t.endswith(".0") else t


def fmt_unit(n: int | None, unit: str = "auto") -> str:
    """원 → 고른 단위의 글(천 단위 쉼표). 자동은 short_money, 천원은 정수, 백만원·억원은 소수 한 자리(끝 0 빼기). 빈 값은 빈 글."""
    if n is None:
        return ""
    div = UNITS.get(unit, UNITS["auto"])[0]
    if div == 0:
        return short_money(n)
    if div == 1:
        return f"{n:,}"
    if div == 1000:
        return f"{round(n / 1000):,}"
    return _one(n / div)


def clean_name(t: str) -> str:
    return _LEAD_YEAR.sub("", " ".join(str(t or "").split())).strip()


def _columns(head: list[str]) -> dict[str, list[int]]:
    """머리줄 → {열쇠: [열 번호…]}. 같은 이름이 가로로 이어진 칸(병합)은 한 열쇠의 여러 칸이 된다.
    예산은 「합계」로 시작하는 칸 가운데 맨 왼쪽(그해 합계), 그다음 「합계」 칸은 이월금까지 더한 합계(budget_all)."""
    col: dict[str, list[int]] = {}
    taken: set[int] = set()
    for key, words in HEAD:
        exact = EXACT.get(key, ())
        for j, c in enumerate(head):
            f = _flat(c)
            if j in taken or not f:
                continue
            if not (f in exact or any(w in f for w in words)):
                continue
            if key in col and not (col[key][-1] == j - 1 and _flat(head[j - 1]) == f):
                continue                                    # 첫 칸(과 가로로 이어진 같은 이름 칸)만
            col.setdefault(key, []).append(j)
            taken.add(j)
    tot = [j for j, c in enumerate(head) if _flat(c).startswith("합계") and j not in taken]
    for key, j in zip(("budget", "budget_all"), tot):
        col[key] = [j]
        taken.add(j)
    return col


# ---------------------------------------------------------------- 파일 꼴

class Sheet:
    """시트 하나 — 값 격자(rows)와 병합 범위(merges: 0부터 센 (r0, c0, r1, c1))."""

    def __init__(self, title: str, rows: list[list], merges: list[tuple[int, int, int, int]] | None = None):
        self.title, self.rows, self.merges = title, rows, merges or []


# 자원 상한 — 올린 파일이 커도 서버가 버티게(넘으면 친절한 오류). 실제 현황 표는 수십 줄·스무 몇 열이다
MAX_BYTES = 10 * 1024 * 1024          # 파일 하나
MAX_SHEETS = 20                       # 시트 수
MAX_ROWS = 5000                       # 시트마다 읽는 줄
MAX_COLS = 200                        # 머리줄 기준으로 실제 쓰는 열까지만, 그래도 이 넘으면 자른다
MAX_ZIP_ITEMS = 2000                  # xlsx(zip) 안 항목 수
MAX_ZIP_TOTAL = 150 * 1024 * 1024     # xlsx(zip) 풀린 전체 크기
MAX_ZIP_RATIO = 200                   # 항목 하나의 압축률(압축 폭탄)


class SheetError(ValueError):
    """읽기를 멈추는 까닭(사람이 읽는 말) — 상한 초과·꼴 모름·사업 현황 표 아님."""


def _too_big(what: str) -> SheetError:
    return SheetError(f"{what} — 파일을 나누거나 필요한 시트만 남겨 다시 올려 주세요")


def _check_zip(p: Path) -> None:
    """압축 폭탄 막기 — 열기 전에 zip 목차만 보고 항목 수·풀린 크기·압축률을 잰다."""
    with zipfile.ZipFile(p) as z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ITEMS:
            raise _too_big(f"엑셀 안 항목이 너무 많습니다({len(infos)}개)")
        total = sum(i.file_size for i in infos)
        if total > MAX_ZIP_TOTAL:
            raise _too_big(f"엑셀을 풀면 {total // (1024 * 1024)}MB 로 너무 큽니다")
        for i in infos:
            if i.file_size > 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > MAX_ZIP_RATIO:
                raise _too_big("엑셀 안 압축률이 비정상으로 큽니다")


def _cap(rows: list[list]) -> list[list]:
    """줄·열 상한 — 줄은 MAX_ROWS, 열은 MAX_COLS 까지만."""
    return [list(r[:MAX_COLS]) for r in rows[:MAX_ROWS]]


def file_kind(path) -> str:
    """파일 앞머리로 가린 꼴 — xlsx · xls · html · mht · xmlss · lock(엑셀이 열어 둔 잠금 파일) · unknown."""
    p = Path(path)
    if p.name.startswith("~$"):
        return "lock"
    head = p.read_bytes()[:4096]
    if head[:4] == b"PK\x03\x04" and zipfile.is_zipfile(p):
        return "xlsx"
    if head[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        return "xls"
    low = head.decode("utf-8", "ignore").lower() + head.decode("cp949", "ignore").lower()
    if "mime-version" in low and ("multipart" in low or "text/html" in low):
        return "mht"
    if "urn:schemas-microsoft-com:office:spreadsheet" in low and "<workbook" in low:
        return "xmlss"
    if "<html" in low or "<table" in low:
        return "html"
    return "unknown"


_MERGE = re.compile(rb'<(?:\w+:)?mergeCell\s+ref="([A-Z]+\d+(?::[A-Z]+\d+)?)"')


def _xlsx_merges(p: Path, member: str | None) -> list[tuple[int, int, int, int]]:
    """시트 XML 의 mergeCell 범위 — 읽기 전용 열기에는 병합 정보가 없어 XML 을 조각으로 훑는다(통째로 올리지 않는다)."""
    from openpyxl.utils.cell import range_boundaries
    if not member:
        return []
    out, tail = [], b""
    with zipfile.ZipFile(p) as z:
        try:
            f = z.open(member.lstrip("/"))
        except KeyError:
            return []
        with f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                for m in _MERGE.finditer(buf):
                    ref = m.group(1).decode()
                    c0, r0, c1, r1 = range_boundaries(ref if ":" in ref else f"{ref}:{ref}")
                    out.append((r0 - 1, c0 - 1, r1 - 1, c1 - 1))
                tail = buf[-200:]
    return list(dict.fromkeys(out))


def _load_xlsx(p: Path) -> list[Sheet]:
    """읽기 전용으로 연다 — 서식만 있는 수천 열(실제 원본도 16,382 열로 보고된다)은 머리줄 기준으로 쓰는 열까지만 읽는다."""
    import openpyxl
    _check_zip(p)
    wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
    try:
        if len(wb.worksheets) > MAX_SHEETS:
            raise _too_big(f"시트가 너무 많습니다({len(wb.worksheets)}개)")
        out = []
        for ws in wb.worksheets:
            top = [list(r) for r in ws.iter_rows(min_row=1, max_row=20, max_col=MAX_COLS, values_only=True)]
            width = max((max((j + 1 for j, v in enumerate(r) if v not in (None, "")), default=0) for r in top), default=0)
            rows = []
            if width:
                for i, r in enumerate(ws.iter_rows(max_col=min(width, MAX_COLS), values_only=True)):
                    if i >= MAX_ROWS:
                        break
                    rows.append(list(r))
            merges = [m for m in _xlsx_merges(p, getattr(ws, "_worksheet_path", None)) if m[0] < MAX_ROWS and m[1] < MAX_COLS]
            out.append(Sheet(ws.title, rows, merges))
        return out
    finally:
        wb.close()


def _load_xls(p: Path) -> list[Sheet]:
    try:
        import xlrd                                       # 선택 의존 — 없으면 아래 안내
    except ImportError:
        raise ValueError("옛 엑셀(.xls) 형식입니다 — 엑셀에서 「다른 이름으로 저장 → Excel 통합 문서(.xlsx)」로 저장해 올려 주세요")
    book = xlrd.open_workbook(str(p), formatting_info=True)
    if book.nsheets > MAX_SHEETS:
        raise _too_big(f"시트가 너무 많습니다({book.nsheets}개)")
    out = []
    for sh in book.sheets():
        rows = []
        for i in range(min(sh.nrows, MAX_ROWS)):
            r = []
            for j in range(min(sh.ncols, MAX_COLS)):
                c = sh.cell(i, j)
                if c.ctype == xlrd.XL_CELL_DATE:
                    try:
                        r.append(xlrd.xldate_as_datetime(c.value, book.datemode))
                        continue
                    except Exception:
                        pass
                r.append(None if c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) else c.value)
            rows.append(r)
        merges = [(r0, c0, r1 - 1, c1 - 1) for r0, r1, c0, c1 in sh.merged_cells]
        out.append(Sheet(sh.name, rows, merges))
    return out


class _TableParser(HTMLParser):
    """html 표 → 격자. colspan·rowspan 은 병합 범위로."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[list[list], list]] = []
        self._rows: list[list] | None = None
        self._merges: list = []
        self._busy: dict[tuple[int, int], bool] = {}
        self._cell: list[str] | None = None
        self._span = (1, 1)
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows, self._merges, self._busy = [], [], {}
        elif self._rows is not None and self._depth == 1:
            if tag == "tr" and len(self._rows) <= MAX_ROWS:
                self._rows.append([])
            elif tag in ("td", "th"):
                if not self._rows:
                    self._rows.append([])
                if len(self._rows) > MAX_ROWS or len(self._rows[-1]) >= MAX_COLS:
                    return                                   # 상한 밖 칸은 버린다
                self._cell = []

                def num(k):
                    try:
                        return max(1, int(str(a.get(k) or "1").strip()))
                    except ValueError:
                        return 1
                self._span = (min(num("rowspan"), MAX_ROWS), min(num("colspan"), MAX_COLS))
            elif tag == "br" and self._cell is not None:
                self._cell.append("\n")

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 1 and self._rows is not None:
                self.tables.append((self._rows, self._merges))
                self._rows = None
            self._depth = max(0, self._depth - 1)
        elif tag in ("td", "th") and self._cell is not None and self._rows is not None:
            i = len(self._rows) - 1
            row = self._rows[i]
            j = len(row)
            while self._busy.get((i, j)):
                row.append(None)
                j += 1
            rs, cs = self._span
            row.append("".join(self._cell).strip() or None)
            for dj in range(1, cs):
                row.append(None)
            for di in range(rs):
                for dj in range(cs):
                    if di or dj:
                        self._busy[(i + di, j + dj)] = True
            if rs > 1 or cs > 1:
                self._merges.append((i, j, i + rs - 1, j + cs - 1))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _html_sheets(text: str, title: str) -> list[Sheet]:
    p = _TableParser()
    p.feed(text)
    p.close()
    out = []
    if len(p.tables) > MAX_SHEETS:
        raise _too_big(f"표가 너무 많습니다({len(p.tables)}개)")
    for n, (rows, merges) in enumerate(p.tables, 1):
        rows = _cap(rows)
        width = max((len(r) for r in rows), default=0)
        rows = [r + [None] * (width - len(r)) for r in rows]
        out.append(Sheet(title if len(p.tables) == 1 else f"{title} {n}", rows, merges))
    return out


def _decode(raw: bytes) -> str:
    m = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", raw[:4096])
    for enc in ([m.group(1).decode()] if m else []) + ["utf-8", "cp949"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "ignore")


def _load_html(p: Path) -> list[Sheet]:
    return _html_sheets(_decode(p.read_bytes()), p.stem)


def _load_mht(p: Path) -> list[Sheet]:
    import email
    msg = email.message_from_bytes(p.read_bytes())
    out = []
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            raw = part.get_payload(decode=True) or b""
            cs = part.get_content_charset()
            text = raw.decode(cs, "ignore") if cs else _decode(raw)
            out.extend(_html_sheets(text, p.stem))
    return out


def _load_xmlss(p: Path) -> list[Sheet]:
    """XML 스프레드시트(엑셀 2003 XML) — Worksheet/Table/Row/Cell(ss:Index·MergeAcross·MergeDown)."""
    import xml.etree.ElementTree as ET
    ns = "{urn:schemas-microsoft-com:office:spreadsheet}"
    root = ET.fromstring(p.read_bytes())
    out = []
    for ws in root.iter(ns + "Worksheet"):
        rows: list[list] = []
        merges = []
        busy: set[tuple[int, int]] = set()
        tbl = ws.find(ns + "Table")
        i = -1
        for row in (tbl.findall(ns + "Row") if tbl is not None else []):
            i = int(row.get(ns + "Index")) - 1 if row.get(ns + "Index") else i + 1
            if i >= MAX_ROWS:
                break
            while len(rows) <= i:
                rows.append([])
            r = rows[i]
            j = -1
            for cell in row.findall(ns + "Cell"):
                j = int(cell.get(ns + "Index")) - 1 if cell.get(ns + "Index") else j + 1
                while (i, j) in busy:
                    j += 1
                if j >= MAX_COLS:
                    break
                d = cell.find(ns + "Data")
                v = None
                if d is not None:
                    t = "".join(d.itertext())
                    typ = d.get(ns + "Type")
                    if typ == "Number":
                        try:
                            v = float(t)
                        except ValueError:
                            v = t
                    elif typ == "DateTime":
                        try:
                            v = _dt.datetime.fromisoformat(t[:19])
                        except ValueError:
                            v = t
                    else:
                        v = t
                while len(r) <= j:
                    r.append(None)
                r[j] = v
                a, dn = min(int(cell.get(ns + "MergeAcross") or 0), MAX_COLS), min(int(cell.get(ns + "MergeDown") or 0), MAX_ROWS)
                if a or dn:
                    merges.append((i, j, i + dn, j + a))
                    for di in range(dn + 1):
                        for dj in range(a + 1):
                            if di or dj:
                                busy.add((i + di, j + dj))
                j += a
        rows = _cap(rows)
        width = max((len(r) for r in rows), default=0)
        out.append(Sheet(ws.get(ns + "Name") or p.stem, [r + [None] * (width - len(r)) for r in rows], merges))
    return out


def load_sheets(path) -> list[Sheet]:
    """파일 → 시트들. 꼴은 앞머리로 가린다(확장자가 틀려도 읽는다)."""
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise _too_big(f"파일이 {p.stat().st_size // (1024 * 1024)}MB 로 {MAX_BYTES // (1024 * 1024)}MB 보다 큽니다")
    kind = file_kind(p)
    if kind == "lock":
        raise SheetError("엑셀이 열어 둔 잠금 파일(~$)입니다 — 원래 파일을 고르세요")
    loader = {"xlsx": _load_xlsx, "xls": _load_xls, "html": _load_html, "mht": _load_mht, "xmlss": _load_xmlss}.get(kind)
    if loader is None:
        raise SheetError("엑셀 파일 꼴을 알아보지 못했습니다(xlsx·xls·웹 페이지로 저장한 html 을 읽습니다)")
    return loader(p)


def header_row(sh: Sheet) -> int | None:
    """「사업명」 머리줄 번호(0부터) — 위 15줄 안에서. 없으면 사업 현황 표가 아니다."""
    return next((i for i, r in enumerate(sh.rows[:15]) if any("사업명" in _flat(c) for c in r)), None)


def probe(path) -> dict:
    """사업 현황 엑셀인가 — {ok, year, error}. 문서함 목록 거르기에 쓴다(결과는 부르는 쪽이 파일 지문으로 캐시)."""
    try:
        sheets = load_sheets(path)
    except Exception as e:                                 # 꼴 모름·상한·깨진 파일
        return {"ok": False, "year": None, "error": str(e) if isinstance(e, SheetError) else type(e).__name__}
    for sh in sheets:
        hi = header_row(sh)
        if hi is None:
            continue
        top = " ".join(_text(c) for r in sh.rows[:hi] for c in r if c not in (None, ""))
        m = _YEAR_TITLE.search(top) or _YEAR_ANY.search(sh.title or "") or _YEAR_ANY.search(Path(path).stem)
        return {"ok": True, "year": int(m.group(1)) if m else None, "error": ""}
    return {"ok": False, "year": None, "error": NOT_SHEET}


NOT_SHEET = "사업 현황 엑셀이 아닙니다(사업명 머리줄 없음)"


_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v):
    """CSV 칸 — 수식으로 읽힐 글(= + - @ 탭·CR 로 시작)은 앞에 작은따옴표. 숫자는 그대로."""
    if isinstance(v, str) and v.startswith(_FORMULA_LEAD):
        return "'" + v
    return v


def _grid(sh: Sheet) -> tuple[list[list], set[tuple[int, int]], dict[tuple[int, int], int]]:
    """시트 값 격자 — 병합 칸은 왼쪽 위 값으로 채운다. (격자, 채운 칸 좌표, 칸 → 가로 병합 너비)."""
    rows = [list(r) for r in sh.rows]
    width = 0
    for r in rows:                                          # 뒤쪽 빈 열은 잘라 낸다(서식만 있는 수천 열)
        k = len(r)
        while k and r[k - 1] in (None, ""):
            k -= 1
        width = max(width, k)
    rows = [(r + [None] * width)[:width] for r in rows]
    filled: set[tuple[int, int]] = set()
    span: dict[tuple[int, int], int] = {}
    for r0, c0, r1, c1 in sh.merges:
        if r0 >= len(rows) or c0 >= width:
            continue
        v = rows[r0][c0]
        for i in range(r0, min(r1, len(rows) - 1) + 1):
            for j in range(c0, min(c1, width - 1) + 1):
                span[(i, j)] = c1 - c0 + 1
                if (i, j) != (r0, c0):
                    rows[i][j] = v
                    filled.add((i, j))
    return rows, filled, span


def _group_heads(rows: list[list], hi: int, col: dict[str, list[int]]) -> dict[str, str]:
    """2단 머리줄의 윗줄 묶음 이름 — 정보 열(사업현황)·금액 열(OOOO년 사업예산(원))·구분 열 위의 글."""
    out = {}
    if hi == 0:
        return out
    up = rows[hi - 1]
    for kind, keys in (("info", ("name", "sub", "ministry", "manager")), ("money", ("national", "budget")), ("status", ("status",))):
        for k in keys:
            js = col.get(k) or []
            if js and js[0] < len(up) and _text(up[js[0]]):
                out[kind] = _head_text(up[js[0]])
                break
    return out


def read_sheets(path, hint: str = "") -> tuple[list[dict], list[dict]]:
    """엑셀 한 권 → (연도별 줄들, 시트 정보들).
    줄: {year, name, seq, sub, ministry, agency, period, year_period, manager, officer, dept, account_dept, majors, status,
        fiscal_year, budget·national·local·company·school·other·incentive·carry·budget_all·half(글과 _won), extra, file, sheet, line}
    시트 정보: {year, file, sheet, title, base_date, target, heads{info, money, status}, columns[[열쇠, 원본 머리줄 글]], total{_won…}, lines}.
    hint 는 연도를 못 찾을 때 볼 문서 이름."""
    items, metas = [], []
    label = Path(path).name if not hint else hint
    for sh in load_sheets(path):
        rows, filled, span = _grid(sh)
        hi = header_row(Sheet(sh.title, rows))
        if hi is None:
            continue
        top_cells = [_head_text(c) for r in rows[:hi] for c in r if c not in (None, "")]
        top = " ".join(dict.fromkeys(top_cells))
        m = _YEAR_TITLE.search(top) or _YEAR_ANY.search(sh.title or "") or _YEAR_ANY.search(hint or "") \
            or _YEAR_ANY.search(Path(path).stem)
        if not m:
            continue
        year = int(m.group(1))
        raw_head = [_head_text(c) for c in rows[hi]]
        head = [_text(c) for c in rows[hi]]
        col = _columns(head)
        if "name" not in col:
            continue
        nj = col["name"][0]
        used = {j for js in col.values() for j in js}
        key_of = {j: k for k, js in col.items() for j in js}
        columns, last = [], None
        for j, h in enumerate(raw_head):
            k = key_of.get(j) or (f"x:{head[j]}" if head[j] and head[j] not in ("순", "번호") else None)
            if k and k != last:
                columns.append([k, h])
            last = k
        bj = (col.get("budget") or [None])[0]
        title = next((_text(c) for c in top_cells if _YEAR_TITLE.search(c) and len(c) < 80), "")
        bm = next((x for x in (_BASE.search(c) for c in top_cells) if x), None)
        tm = next((x for x in (_TARGET.search(c) for c in top_cells) if x), None)
        meta = {"year": year, "file": label, "sheet": sh.title, "title": title,
                "base_date": fmt_period([bm.group(1)]) if bm else "",
                "target": " ".join(tm.group(1).split()).strip(" .") if tm else "",
                "heads": _group_heads(rows, hi, col), "columns": columns, "total": {}, "lines": 0}
        got = []
        for i in range(hi + 1, len(rows)):
            r = rows[i]
            name = clean_name(_text(r[nj]))
            lead_total = any(_TOTAL.match(_text(r[j])) for j in range(0, nj))
            wide = span.get((i, nj), 1) >= 3
            if not name or len(name) < 3 or name.startswith(_NOTE) or _TOTAL.match(name) or wide or lead_total:
                # 합계 줄 — 금액 칸에 수가 있고, 「합계·총계」 글이 있거나 금액 밖 칸에 글이 없다(병합만 한 빈 줄)
                if got and bj is not None and money(r[bj]) is not None and not meta["total"]:
                    words = [_text(r[j]) for j in range(len(r)) if j not in {x for k in MONEY for x in col.get(k, [])}]
                    if any(_GRAND.match(w) for w in words if w) or not any(w for w in words if money(w) is None):
                        meta["total"] = {k + "_won": money(r[col[k][0]]) for k in MONEY if col.get(k) and money(r[col[k][0]]) is not None}
                        break
                continue

            def get(key, r=r, i=i):
                js = col.get(key) or []
                if key in MONEY:
                    js = [j for j in js if (i, j) not in filled]
                return [r[j] for j in js]

            item = {"year": year, "name": name, "file": label, "sheet": sh.title, "line": i + 1,
                    "seq": next((t for t in (_text(v) for v in get("seq")) if t), "")}
            for key in TEXTS:
                item[key] = ", ".join(dict.fromkeys(t for t in (_text(v) for v in get(key)) if t))
            item["period"] = fmt_period(get("period"))
            item["year_period"] = fmt_period(get("year_period"))
            for key in MONEY:
                n = next((x for x in (money(v) for v in get(key)) if x is not None), None)
                item[key + "_won"], item[key] = n, won(n)
            item["extra"] = {head[j] or f"{j + 1}열": _text(r[j]) for j in range(len(head))
                             if j not in used and _text(r[j]) and head[j] and head[j] not in ("순", "번호")}
            got.append(item)
        meta["lines"] = len(got)
        items.extend(got)
        metas.append(meta)
    return items, metas


def read_book(path, hint: str = "") -> list[dict]:
    """엑셀 한 권 → 연도별 줄(read_sheets 의 첫째 값)."""
    return read_sheets(path, hint)[0]


# ---------------------------------------------------------------- 잇기

class Matcher:
    """사업 관리 줄 찾기 — 이름 열쇠가 같으면 그 줄, 괄호 속 이름이 같으면 그 줄, 아니면 가장 긴 별칭 겹침(같은 길이로 여럿이면 모호)."""

    MIN = 4

    def __init__(self, rows: list[dict], not_program: set[str] | None = None):
        self.exact: dict[str, set[str]] = {}
        self.keys: list[tuple[str, str]] = []
        self.name = {r["key"]: r.get("name") or r["key"] for r in rows}
        self.not_program = not_program or set()
        for r in rows:
            for t in [r.get("name") or "", *(r.get("terms") or [])]:
                k = group_key(t)
                if k:
                    self.exact.setdefault(k, set()).add(r["key"])
                    if len(k) >= self.MIN:
                        self.keys.append((k, r["key"]))

    def find(self, name: str) -> tuple[str, list[str]]:
        """→ (갈래, 줄 열쇠들). 갈래: exact · inner · contains · ambiguous · new · not_program."""
        k = group_key(name)
        if not k:
            return "new", []
        hit = self.exact.get(k) or set()
        if len(hit) == 1:
            return "exact", sorted(hit)
        if len(hit) > 1:
            return "ambiguous", sorted(hit)
        if k in self.not_program:
            return "not_program", []
        for part in re.findall(r"\(([^)]{4,})\)", name or ""):
            h = self.exact.get(group_key(part)) or set()
            if len(h) == 1:
                return "inner", sorted(h)
        best, got = 0, set()
        for t, key in self.keys:
            if t in k or (len(k) >= 6 and k in t):
                n = min(len(t), len(k))
                if n > best:
                    best, got = n, {key}
                elif n == best:
                    got.add(key)
        if len(got) == 1:
            return "contains", sorted(got)
        if len(got) > 1:
            return "ambiguous", sorted(got)
        return "new", []


def _join(a: str, b: str) -> str:
    parts = [p for p in re.split(r"\s*,\s*", a or "") if p] + [p for p in re.split(r"\s*,\s*", b or "") if p]
    return ", ".join(dict.fromkeys(parts))


def _fold(items: list[dict]) -> dict:
    """같은 사업·같은 해의 여러 줄(세부 과제) → 한 칸 묶음. 금액은 더하고 글은 겹치지 않게 잇는다."""
    out: dict = {"lines": len(items)}
    for f in YEAR_FIELDS:
        if f in MONEY:
            ns = [it.get(f + "_won") for it in items if it.get(f + "_won") is not None]
            if ns:
                out[f + "_won"] = sum(ns)
                out[f] = won(out[f + "_won"])
            continue
        v = ""
        for it in items:
            v = _join(v, it.get(f) or "")
        if v:
            out[f] = v
    extra: dict = {}
    for it in items:
        for h, v in (it.get("extra") or {}).items():
            extra[h] = _join(extra.get(h, ""), v)
    if extra:
        out["extra"] = extra
    out["from"] = ", ".join(dict.fromkeys(str(it.get("file") or "") for it in items if it.get("file")))
    its = sorted(items, key=lambda it: (str(it.get("file") or ""), int(it.get("line") or 0)))
    out["tasks"] = [task(it) for it in its]
    out["order"] = min((int(it.get("line") or 0) for it in items), default=0)
    out["seq"] = next((str(it.get("seq")) for it in its if it.get("seq")), "")
    return out


TASK_TEXT = ("name", "seq", "sub", "ministry", "agency", "period", "year_period", "manager", "majors", "officer", "dept",
             "account_dept", "status", "fiscal_year")


def task(it: dict) -> dict:
    """엑셀 한 줄(세부 과제) → 저장할 꼴. 글 칸은 비면 빼고, 금액은 원 단위 수(_won)만."""
    out = {"line": int(it.get("line") or 0)}
    out.update({f: it[f] for f in TASK_TEXT if it.get(f)})
    out.update({f + "_won": it[f + "_won"] for f in MONEY if it.get(f + "_won") is not None})
    if it.get("extra"):
        out["extra"] = dict(it["extra"])
    return out


def latest(years: dict) -> dict:
    """연도별 값 가운데 가장 최근 해의 값."""
    ys = [y for y in (years or {}) if str(y).isdigit()]
    return dict(years[max(ys, key=int)]) if ys else {}


LOG_MAX = 300


def add_log(o: dict, by: str, field: str, before, after, via: str, year: str = "") -> None:
    """줄 변경 기록 — 누가·언제·무엇(전→후)·어떤 경로(직접·가져오기·신청 #n 승인·되돌리기·합치기). 오래된 것부터 버린다."""
    e = {"at": time.strftime("%Y-%m-%d %H:%M"), "by": by, "field": field, "before": before, "after": after, "via": via}
    if year:
        e["year"] = str(year)
    log = o.setdefault("log", [])
    log.append(e)
    del log[:-LOG_MAX]


def plan(items: list[dict], rows: list[dict], store: dict, overwrite: bool = False, not_program: set[str] | None = None,
         by: str = "", sheets: list[dict] | None = None, allow=None, allow_new: bool = True) -> tuple[dict, dict]:
    """엑셀 줄 → (요약, 바뀐 저장값). store 는 고치지 않고 사본을 돌려준다.
    요약: matched·new·ambiguous·not_program(이름 목록), cells{fill, change, same, kept}, blank_top(빈 칸이던 표의 칸 가운데 채워지는 수),
    tasks(보존한 세부 과제 줄 수), by_year{연도: {lines, matched, new, ambiguous, fill, change, skipped}}, skipped(권한 밖이라 건너뛴 이름).
    sheets(read_sheets 의 시트 정보)는 저장값의 sheets[연도] 에 둔다(기준일·대상기간·열 순서·합계 줄).
    allow(줄 열쇠) → 거짓이면 그 줄은 건너뛴다(총괄 책임자의 가져오기 — 자기 부서 사업만). allow_new 가 거짓이면 새 줄을 더하지 않는다."""
    store = copy.deepcopy(store)
    store.setdefault("rows", {})
    store.setdefault("added", {})
    for meta in sheets or []:
        store.setdefault("sheets", {})[str(meta["year"])] = {k: v for k, v in meta.items() if k != "year"}
    m = Matcher(rows, not_program)
    by_key = {r["key"]: r for r in rows}
    groups: dict[str, dict] = {}                       # 줄 열쇠 → {year: [items]}
    new_names: dict[str, list[dict]] = {}              # 이름 열쇠 → items (장부에 없는 사업)
    matched: dict[str, set[str]] = {}
    ambiguous: dict[str, list[str]] = {}
    skipped: set[str] = set()
    denied: set[str] = set()
    by_year: dict[str, dict] = {}

    def yb(it):
        return by_year.setdefault(str(it["year"]), {"lines": 0, "matched": set(), "new": set(), "ambiguous": set(), "fill": 0,
                                                    "change": 0, "skipped": set()})
    for it in items:
        how, keys = m.find(it["name"])
        yb(it)["lines"] += 1
        if how in ("exact", "inner", "contains") and allow is not None and not allow(keys[0]):
            denied.add(it["name"])
            yb(it)["skipped"].add(it["name"])
            continue
        if how == "new" and not allow_new:
            denied.add(it["name"])
            yb(it)["skipped"].add(it["name"])
            continue
        if how in ("exact", "inner", "contains"):
            yb(it)["matched"].add(keys[0])
            groups.setdefault(keys[0], {}).setdefault(str(it["year"]), []).append(it)
            matched.setdefault(keys[0], set()).add(it["name"])
        elif how == "ambiguous":
            ambiguous[it["name"]] = [m.name.get(k, k) for k in keys]
            yb(it)["ambiguous"].add(it["name"])
        elif how == "not_program":
            skipped.add(it["name"])
        else:
            new_names.setdefault(group_key(it["name"]) or it["name"], []).append(it)
            yb(it)["new"].add(group_key(it["name"]) or it["name"])
    new_rows = []
    for gk, its in new_names.items():
        its.sort(key=lambda x: x["year"])
        last = its[-1]
        key = slug(last["name"])
        n = 2
        while key in by_key or key in store["added"]:
            key, n = f"{slug(last['name'])}-{n}", n + 1
        store["added"][key] = {"source": "엑셀", "name": last["name"],
                               "terms": list(dict.fromkeys(x["name"] for x in its)),
                               "dept": last.get("dept") or "", "period": last.get("period") or "",
                               "added_by": by, "added_at": time.strftime("%Y-%m-%d")}
        for it in its:
            groups.setdefault(key, {}).setdefault(str(it["year"]), []).append(it)
        new_rows.append({"key": key, "name": last["name"], "years": sorted({x["year"] for x in its})})
    cells = {"fill": 0, "change": 0, "same": 0, "kept": 0}
    blank_top = n_tasks = 0
    for key, years in groups.items():
        o = store["rows"].setdefault(key, {})
        ys = o.setdefault("years", {})
        for y, its in years.items():
            got = _fold(its)
            cur = ys.setdefault(y, {})
            hand = set(cur.get("by_hand") or [])
            filled = 0
            for f in YEAR_FIELDS:
                v = got.get(f)
                if not v:
                    continue
                old = cur.get(f) or ""
                if old == v:
                    cells["same"] += 1
                    continue
                if f in hand and not overwrite:
                    cells["kept"] += 1
                    continue
                cells["fill" if not old else "change"] += 1
                by_year.setdefault(y, {"fill": 0, "change": 0}).setdefault("fill", 0)
                by_year[y]["fill" if not old else "change"] += 1
                if old:
                    add_log(o, by, f, old, v, "가져오기", y)
                else:
                    filled += 1
                cur[f] = v
                if f in MONEY:
                    cur[f + "_won"] = got.get(f + "_won")
                if f in hand:
                    hand.discard(f)
            if got.get("extra"):
                cur["extra"] = {**(cur.get("extra") or {}), **got["extra"]}
            if filled:
                add_log(o, by, "", "", f"{filled}칸 채움", "가져오기", y)
            cur["tasks"] = got["tasks"]                 # 세부 과제 줄은 엑셀에서만 온다 — 늘 새 엑셀 값으로
            n_tasks += len(got["tasks"])
            cur["order"], cur["seq"] = got["order"], got["seq"]
            cur["lines"] = got["lines"]
            cur["from"] = got.get("from") or ""
            cur["at"] = time.strftime("%Y-%m-%d")
            if hand:
                cur["by_hand"] = sorted(hand)
            else:
                cur.pop("by_hand", None)
        r = by_key.get(key)
        if r is not None:                               # 표에서 빈칸이던 칸 가운데 연도 값으로 채워 보이는 칸
            before, after = latest(r.get("years") or {}), latest(ys)
            blank_top += sum(1 for f in ("dept", "period", "budget", "manager") if not (r.get(f) or before.get(f)) and after.get(f))
    summary = {
        "lines": len(items),
        "years": sorted({it["year"] for it in items}),
        "matched": sorted(((by_key[k]["name"] if k in by_key else k), sorted(v)) for k, v in matched.items()),
        "new": new_rows,
        "ambiguous": sorted(ambiguous.items()),
        "not_program": sorted(skipped),
        "cells": cells,
        "blank_top": blank_top,
        "tasks": n_tasks,
        "skipped": sorted(denied),
        "by_year": {y: {k: (len(v) if isinstance(v, set) else v) for k, v in d.items()} for y, d in sorted(by_year.items())},
        "overwrite": overwrite,
    }
    return summary, store


# ---------------------------------------------------------------- 연도별 보기·내려받기

def year_money(yv: dict) -> dict[str, int | None]:
    """연도 값 → {budget·national·…: 원}. 엑셀에서 온 칸은 _won, 손으로 적은 예산 글(「9억 원」)은 parse_won 으로."""
    out: dict[str, int | None] = {}
    for f in MONEY:
        n = (yv or {}).get(f + "_won")
        if n is None and (yv or {}).get(f):
            n = parse_won(yv[f])
        out[f] = n
    return out


def chips(status: str) -> list[str]:
    """구분 글(「기존, 신규」) → 칩 목록."""
    return [p for p in dict.fromkeys(re.split(r"\s*[,/·]\s*", str(status or "").strip())) if p]


def _group_of(key: str) -> str:
    if key == "status":
        return "status"
    if key.startswith("x:"):
        return key
    return "money" if key in MONEY else "info"


def write_book(year: int, lines: list[dict], meta: dict | None = None) -> bytes:
    """연도 하나의 줄들 → 원본과 같은 꼴의 xlsx(제목·기준 줄, 2단 머리줄, 원본 열 순서, 맨 아래 합계 줄, 금액 천 단위 쉼표).
    lines: 세부 과제 꼴의 dict(글 칸 + 금액 _won). meta: 저장값 sheets[연도](없으면 기본 열)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    meta = meta or {}
    wb = openpyxl.Workbook()
    _write_sheet(wb.active, year, lines, meta)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def write_books(years: list[tuple[int, list[dict], dict | None]]) -> bytes:
    """여러 해 → 연도마다 시트 하나인 xlsx(전체 연도 내보내기)."""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for year, lines, meta in years:
        _write_sheet(wb.create_sheet(str(year)), year, lines, meta or {})
    if not wb.worksheets:
        wb.create_sheet("빈 표")
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def put(c, v) -> None:
    """칸에 값 — 글은 늘 문자열형으로(= 로 시작해도 수식이 되지 않게), 수는 수형."""
    c.value = v
    if isinstance(v, str):
        c.data_type = "s"


def _write_sheet(ws, year: int, lines: list[dict], meta: dict) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    cols = [(str(k), str(h)) for k, h in (meta.get("columns") or [])]
    if not any(k == "name" for k, _ in cols):
        cols = [c for c in DEFAULT_COLUMNS if c[0] not in ("fiscal_year", "incentive", "account_dept", "status")
                or any(ln.get(c[0]) or ln.get(c[0] + "_won") for ln in lines)]
    if not any(k == "budget" for k, _ in cols):
        cols.append(("budget", "합계"))
    n = len(cols)
    heads = meta.get("heads") or {}
    ws.title = str(year)
    thin = Side(style="thin", color="B8BEC8")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill("solid", fgColor="EEF1F5")
    wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)
    put(ws.cell(1, 1), meta.get("title") or f"{year}학년도 재정지원사업 수혜 현황")
    ws.cell(1, 1).font = Font(size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n)
    if meta.get("target"):
        put(ws.cell(2, 1), f"▣ 사업선정 대상기간 : {meta['target']}")
    if meta.get("base_date"):
        put(ws.cell(2, n), f"{meta['base_date']} 기준")
        ws.cell(2, n).alignment = Alignment(horizontal="right")
    if n > 2:
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n - 1)
    group_text = {"status": heads.get("status") or "구분", "info": heads.get("info") or "사업현황",
                  "money": heads.get("money") or f"{year}년 사업예산(원)"}
    j = 0
    while j < n:                                           # 윗줄 — 같은 묶음끼리 가로로 합친다, 묶음 없는 열은 위아래로
        g = _group_of(cols[j][0])
        k = j
        while k + 1 < n and _group_of(cols[k + 1][0]) == g and not g.startswith("x:"):
            k += 1
        if g.startswith("x:"):
            put(ws.cell(3, j + 1), cols[j][1])
            ws.merge_cells(start_row=3, start_column=j + 1, end_row=4, end_column=j + 1)
        else:
            put(ws.cell(3, j + 1), group_text[g])
            if k > j:
                ws.merge_cells(start_row=3, start_column=j + 1, end_row=3, end_column=k + 1)
        j = k + 1
    for j, (k, h) in enumerate(cols, 1):
        if not k.startswith("x:"):
            put(ws.cell(4, j), h)
    for r in (3, 4):
        for j in range(1, n + 1):
            c = ws.cell(r, j)
            c.alignment, c.fill, c.border = wrap, fill, box
    money_cols = [j for j, (k, _) in enumerate(cols, 1) if k in MONEY]
    row = 5
    for i, ln in enumerate(lines, 1):
        for j, (k, _) in enumerate(cols, 1):
            if k == "seq":
                v = i
            elif k in MONEY:
                v = ln.get(k + "_won")
                v = v if isinstance(v, int) and not isinstance(v, bool) else None   # 금액 칸은 수만
            elif k.startswith("x:"):
                v = (ln.get("extra") or {}).get(k[2:]) or None
            elif k == "fiscal_year":
                t = str(ln.get(k) or "")
                v = int(t) if t.isdigit() else (t or None)
            else:
                v = ln.get(k) or None
            c = ws.cell(row, j)
            put(c, v)
            c.border = box
            if k in MONEY:
                c.number_format = "#,##0"
            elif k in ("seq", "status", "fiscal_year"):
                c.alignment = Alignment(horizontal="center", vertical="center")
            else:
                c.alignment = Alignment(vertical="center", wrap_text=True)
        row += 1
    first_money = money_cols[0] if money_cols else n + 1
    ws.cell(row, 1, "합계").alignment = Alignment(horizontal="center", vertical="center")
    if first_money > 2:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=first_money - 1)
    for j in range(1, n + 1):
        c = ws.cell(row, j)
        c.fill, c.border = fill, box
        k = cols[j - 1][0]
        if k in MONEY:
            got = [ln.get(k + "_won") for ln in lines if ln.get(k + "_won") is not None]
            c.value = sum(got) if got else 0
            c.number_format = "#,##0"
    width = {"name": 30, "sub": 34, "majors": 22, "period": 22, "year_period": 22, "ministry": 14, "agency": 16, "dept": 16,
             "account_dept": 14, "seq": 5, "status": 9, "fiscal_year": 8}
    for j, (k, _) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(j)].width = width.get(k, 16 if k in MONEY else 11)
    ws.freeze_panes = ws.cell(5, 1)


# ---------------------------------------------------------------- 입력 검사·짧은 꼴

def _ymd(t: str) -> _dt.date | None:
    ds = _dates(t)
    if not ds:
        return None
    y, m, d = (int(x) for x in ds[0].split("."))
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def norm_period(text: str) -> tuple[str, str]:
    """기간 글 → (「YYYY.MM.DD~YYYY.MM.DD」 꼴, 오류). 「2025.3.1~2026.2.28」「2025-03-01 ~ 2026-02-28」「20250301-20260228」,
    연도만(「2025~2027」·끝이 열린 「2025~」)도 받는다. 날짜가 없거나 끝이 시작보다 앞이면 오류."""
    t = " ".join(str(text or "").split())
    if not t:
        return "", ""
    parts = [p.strip() for p in re.split(r"\s*[~∼]\s*|(?<=\d)\s*-\s*(?=(?:19|20)\d{2}\D|(?:19|20)\d{6})", t, maxsplit=1)]
    if all(re.fullmatch(r"(?:19|20)\d{2}(?:년)?", p) or p in ("", "진행 중", "진행중") for p in parts) and parts[0]:
        a = int(parts[0][:4])
        b = parts[1][:4] if len(parts) > 1 and parts[1][:4].isdigit() else ""
        if b and int(b) < a:
            return t, "끝 연도가 시작 연도보다 앞입니다"
        return f"{a}~{b}", ""
    ds = [_ymd(p) for p in parts if p]
    if not ds or any(d is None for d in ds):
        return t, "날짜를 읽지 못했습니다(예: 2025.03.01~2026.02.28)"
    if len(ds) == 2 and ds[1] < ds[0]:
        return t, "끝 날짜가 시작 날짜보다 앞입니다"
    f = lambda d: f"{d.year}.{d.month:02d}.{d.day:02d}"
    return (f(ds[0]) if len(ds) == 1 else f"{f(ds[0])}~{f(ds[1])}"), ""


def short_period(text: str) -> str:
    """표에 넣을 짧은 기간 — 「2025.03.01~2030.02.28」 → 「2025.03~2030.02」."""
    return re.sub(r"((?:19|20)\d{2})\.(\d{2})\.\d{2}", r"\1.\2", str(text or ""))


def short_money(n: int | None) -> str:
    """자동 단위 금액 — 1억 이상 「53.1억」, 1만~1억 미만 「8,190만」, 그 아래 「900」(원). 음수도 같은 규칙, 빈 값은 빈 글."""
    if n is None:
        return ""
    if abs(n) >= 10 ** 8 or round(abs(n) / 10 ** 4, 1) >= 10 ** 4:   # 99,999,999 는 「10,000만」이 아니라 「1억」
        return _one(n / 10 ** 8) + "억"
    if abs(n) >= 10 ** 4:
        return _one(n / 10 ** 4) + "만"
    return f"{n:,}"
