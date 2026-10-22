"""한글 작업본의 표 서식 — 학교 서식에서 캔 표 모양(머리줄·라벨 칸·선·글자)을 입히고, 담당자 요청대로 고친다.

기준(학교 서식·문서 1,459건의 표 20,441개 채굴, 10-22):
- 머리줄·라벨 칸은 옅은 회색 음영(#E5E5E5 가 가장 흔함)·가운데 정렬, 머리줄 글자는 굵게. 본문 칸은 음영 없음.
- 선은 안쪽 0.12mm·바깥 0.4mm 실선(머리줄 아래 이중선은 고를 수 있음), 글자는 10pt, 칸 여백 510/141.
- 한 칸 상자(【작성방법】·※ 안내)는 0.12mm 점선, 양쪽 정렬.

입히기는 표마다 칸의 쓰임을 보고(머리줄=첫 행이 모두 글이고 아래 행이 있음, 라벨=글 있는 칸 오른쪽이 빈 칸·필드) 정한다.
모양(테두리·글자·문단)은 header.xml 에 새 항목으로 더하고 같은 모양은 다시 쓴다 — 양식에 원래 있던 모양은 건드리지 않는다.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

HOUSE = {"head_fill": "#E5E5E5", "label_fill": "#E5E5E5", "outer": "0.4 mm", "inner": "0.12 mm",
         "head_bottom": "SOLID", "font_pt": 10.0, "head_bold": True, "head_align": "CENTER", "body_align": ""}
BORDER_WIDTHS = ["0.1 mm", "0.12 mm", "0.15 mm", "0.2 mm", "0.25 mm", "0.3 mm", "0.4 mm", "0.5 mm", "0.6 mm", "0.7 mm",
                 "1.0 mm", "1.5 mm", "2.0 mm", "3.0 mm", "4.0 mm", "5.0 mm"]
ALIGNS = {"가운데": "CENTER", "왼쪽": "LEFT", "오른쪽": "RIGHT", "양쪽": "JUSTIFY",
          "CENTER": "CENTER", "LEFT": "LEFT", "RIGHT": "RIGHT", "JUSTIFY": "JUSTIFY"}
_TBL = re.compile(r"<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>", re.S)          # 안쪽(중첩 없는) 표
_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_TR = re.compile(r"<hp:tr\b.*?</hp:tr>", re.S)


def _text(x: str) -> str:
    x = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", x, flags=re.S)       # 필드 안내 글은 칸 글이 아니다
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip()


def _short(t: str) -> bool:
    """가운데에 둘 값 — 기호(●○◎-), 숫자·금액·비율·날짜, 여섯 글자 이하."""
    t = (t or "").strip()
    return bool(t) and (len(t) <= 6 or bool(re.fullmatch(r"[\d\s,.%~\-–()/:·년월일천백만억원명개건회점]+", t)))


def _width(w: str) -> str:
    """'0.4', '0.4mm', 0.4 → 한글이 아는 가장 가까운 굵기('0.4 mm')."""
    try:
        v = float(re.sub(r"[^\d.]", "", str(w)) or 0)
    except ValueError:
        return HOUSE["inner"]
    return min(BORDER_WIDTHS, key=lambda b: abs(float(b.split()[0]) - v))


def _color(c: str) -> str:
    c = str(c or "").strip()
    named = {"회색": "#E5E5E5", "연회색": "#F2F2F2", "진회색": "#D6D6D6", "파랑": "#E0E5FA", "연파랑": "#DFEAF5",
             "노랑": "#FFF2CC", "연두": "#E2EFDA", "없음": "none", "흰색": "none"}
    if c in named:
        return named[c]
    return c.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else ("none" if c.lower() in ("none", "") else HOUSE["head_fill"])


class _Header:
    """header.xml 에 테두리·글자·문단 모양을 더한다(같은 모양은 한 번만)."""

    def __init__(self, xml: str):
        self.xml = xml
        self.cache: dict[tuple, str] = {}

    def _next_id(self, tag: str) -> int:
        return max([int(x) for x in re.findall(rf'<hh:{tag}\b[^>]*?\bid="(\d+)"', self.xml)] or [0]) + 1

    def _append(self, group: str, body: str) -> None:
        close = self.xml.find(f"</hh:{group}>")
        self.xml = self.xml[:close] + body + self.xml[close:]
        item = {"borderFills": "borderFill", "charProperties": "charPr", "paraProperties": "paraPr"}[group]
        blk = re.search(rf"<hh:{group}\b.*?</hh:{group}>", self.xml, re.S)
        n = len(re.findall(rf"<hh:{item}\b", blk.group(0)))            # 실제 항목 수로(앞선 도구가 틀리게 적은 수도 바로잡힘)
        m = re.search(rf'(<hh:{group}\b[^>]*\bitemCnt=")(\d+)(")', self.xml)
        if m:
            self.xml = self.xml[:m.start(2)] + str(n) + self.xml[m.end(2):]

    def border(self, left: str, right: str, top: str, bottom: str, fill: str, kind: str = "SOLID",
               bottom_kind: str = "") -> str:
        key = ("bf", left, right, top, bottom, fill, kind, bottom_kind)
        if key in self.cache:
            return self.cache[key]
        i = str(self._next_id("borderFill"))

        def side(name: str, w: str, k: str) -> str:
            return f'<hh:{name} type="{k if w else "NONE"}" width="{w or "0.1 mm"}" color="#000000"/>'
        brush = (f'<hc:fillBrush><hc:winBrush faceColor="{fill}" hatchColor="#999999" alpha="0"/></hc:fillBrush>'
                 if fill and fill != "none" else "")
        body = (f'<hh:borderFill id="{i}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
                '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
                + side("leftBorder", left, kind) + side("rightBorder", right, kind) + side("topBorder", top, kind)
                + side("bottomBorder", bottom, bottom_kind or kind)
                + '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>' + brush + "</hh:borderFill>")
        self._append("borderFills", body)
        self.cache[key] = i
        return i

    def char(self, base: str, *, bold: bool | None = None, pt: float | None = None, color: str = "") -> str:
        key = ("cp", base, bold, pt, color)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:charPr\b[^>]*?\bid="{base}"[^>]*>.*?</hh:charPr>', self.xml, re.S)
        if not m:
            return base
        body = m.group(0)
        i = str(self._next_id("charPr"))
        body = re.sub(r'\bid="\d+"', f'id="{i}"', body, count=1)
        if pt:
            body = re.sub(r'\bheight="\d+"', f'height="{int(round(pt * 100))}"', body, count=1)
        if color:
            body = re.sub(r'\btextColor="[^"]*"', f'textColor="{color}"', body, count=1)
        if bold is True and "<hh:bold/>" not in body:
            body = body.replace("<hh:underline", "<hh:bold/><hh:underline", 1)
        elif bold is False:
            body = body.replace("<hh:bold/>", "")
        self._append("charProperties", body)
        self.cache[key] = i
        return i

    def para(self, base: str, align: str) -> str:
        key = ("pp", base, align)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:paraPr\b[^>]*?\bid="{base}"[^>]*>.*?</hh:paraPr>', self.xml, re.S)
        if not m or not align:
            return base
        body = m.group(0)
        if f'horizontal="{align}"' in body:
            self.cache[key] = base
            return base
        i = str(self._next_id("paraPr"))
        body = re.sub(r'\bid="\d+"', f'id="{i}"', body, count=1)
        body = re.sub(r'(<hh:align horizontal=")[A-Z_]+', rf"\g<1>{align}", body, count=1)
        self._append("paraProperties", body)
        self.cache[key] = i
        return i


def _cells(tbl: str) -> list[dict]:
    out = []
    for tc in _TC.finditer(tbl):
        x = tc.group(0)
        a = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', x)
        sp = re.search(r'<hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"', x)
        if not a:
            continue
        out.append({"s": tc.start(), "e": tc.end(), "xml": x, "c": int(a.group(1)), "r": int(a.group(2)),
                    "cs": int(sp.group(1)) if sp else 1, "rs": int(sp.group(2)) if sp else 1,
                    "text": _text(x), "field": "fieldBegin" in x})
    return out


def roles(tbl: str) -> dict[tuple[int, int], str]:
    """칸(행, 열) → 'head'·'label'·'body'·'box'. 머리줄=첫 행이 모두 글이고 아래 행이 있을 때, 라벨=글 있는 칸 바로 오른쪽이 빈 칸·필드."""
    cells = _cells(tbl)
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
        is_label = bool(c["text"]) and not c["field"] and len(c["text"]) <= 20 and (
            (right is not None and (not right["text"] or right["field"]))
            or (c["c"] == 0 and not head_rows and ncols >= 2)
            # 표 안 소머리(아래 칸이 비었거나 필드이고 오른쪽도 글인 칸 — 「국비 | 지방비 | 대응자금」 줄)
            or (below is not None and (not below["text"] or below["field"]) and c["c"] > 0))
        out[(c["r"], c["c"])] = "label" if is_label else "body"
    return out


def style_table(tbl: str, hdr: _Header, opts: dict | None = None) -> str:
    """표 하나에 서식을 입힌다 — opts 는 HOUSE 를 덮는 값(head_fill·label_fill·outer·inner·head_bottom·font_pt·head_bold·
    head_align·body_align)."""
    o = {**HOUSE, **(opts or {})}
    o["outer"], o["inner"] = _width(o["outer"]), _width(o["inner"])
    cells = _cells(tbl)
    if not cells:
        return tbl
    nrows = max(c["r"] + c["rs"] for c in cells)
    ncols = max(c["c"] + c["cs"] for c in cells)
    rl = roles(tbl)
    head_last = max([c["r"] + c["rs"] for c in cells if rl.get((c["r"], c["c"])) == "head"] or [0])
    out, pos = [], 0
    for c in cells:
        role = rl.get((c["r"], c["c"]), "body")
        x = c["xml"]
        if role == "box":                                   # 작성 안내 상자 — 점선, 작은 회색 글, 왼쪽 정렬(본문과 구별)
            bf = hdr.border(o["inner"], o["inner"], o["inner"], o["inner"], "none", "DASH")
            align = "LEFT"
        else:
            left = o["outer"] if c["c"] == 0 else o["inner"]
            right = o["outer"] if c["c"] + c["cs"] >= ncols else o["inner"]
            top = o["outer"] if c["r"] == 0 else o["inner"]
            bottom = o["outer"] if c["r"] + c["rs"] >= nrows else o["inner"]
            fill = o["head_fill"] if role == "head" else o["label_fill"] if role == "label" else "none"
            bk = ""
            if role == "head" and c["r"] + c["rs"] == head_last and o.get("head_bottom") == "DOUBLE":
                bottom, bk = "0.5 mm", "DOUBLE_SLIM"
            bf = hdr.border(left, right, top, bottom, _color(fill), "SOLID", bk)
            align = o["head_align"] if role in ("head", "label") else o.get("body_align") or ""
            if role == "body" and not align and _short(c["text"]):   # 기호·숫자·짧은 값은 가운데(학교 표의 거의 모든 값 칸)
                align = "CENTER"
            # 칸 여백 — 학교 표의 절반이 510/510/141/141(좌우 1.8mm). 좁으면 글이 선에 붙는다
            x = re.sub(r'(<hp:tc\b[^>]*\bhasMargin=")0', r"\g<1>1", x, count=1)
            x = re.sub(r'<hp:cellMargin [^>]*/>', '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>', x, count=1)
            # 줄 높이 — 10pt 한 줄 + 여백이 들어가게 최소 1,900(약 6.7mm). 1,200 이면 글이 위아래 선에 닿는다
            x = re.sub(r'(<hp:cellSz width="\d+" height=")(\d+)"', lambda m: f'{m.group(1)}{max(int(m.group(2)), 1900)}"', x, count=1)
            # 기안기는 「제목 칸」(header="1")의 글자 굵기를 무시한다 — 머리줄 굵게가 보이도록 끈다
            x = re.sub(r'(<hp:tc\b[^>]*\bheader=")1', r"\g<1>0", x, count=1)
        if not o.get("keep_borders"):                        # 양식 표 모양을 따른 표는 선·음영을 그대로 두고 글·정렬·여백만
            x = re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{bf}", x, count=1)
        bold = bool(o["head_bold"]) if role == "head" else None
        pt = float(o["font_pt"]) if o.get("font_pt") else None
        color = ""
        if role == "box":
            pt, color = (pt or 10.0) - 1, "#555555"

        def run(m: re.Match) -> str:
            return f'{m.group(1)}{hdr.char(m.group(2), bold=bold, pt=pt, color=color)}{m.group(3)}'
        x = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")', run, x)
        if align:
            x = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")', lambda m: f"{m.group(1)}{hdr.para(m.group(2), align)}{m.group(3)}", x)
        out.append(tbl[pos:c["s"]] + x)
        pos = c["e"]
    out.append(tbl[pos:])
    t = "".join(out)
    if o.get("keep_borders"):
        return t
    outer_bf = hdr.border(o["outer"], o["outer"], o["outer"], o["outer"], "none")
    return re.sub(r'(<hp:tbl\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{outer_bf}", t, count=1)


def _rewrite(data: bytes, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    hdr = _Header(zin.read("Contents/header.xml").decode("utf-8"))
    new = {}
    for n in zin.namelist():
        if re.match(r"Contents/section\d+\.xml$", n):
            x = zin.read(n).decode("utf-8")
            y = fn(x, hdr, n)
            if y != x:
                new[n] = y
    if not new:
        return data
    new["Contents/header.xml"] = hdr.xml
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def tables(data: bytes) -> list[dict]:
    """작업본의 표 목록 — 번호(1부터, 문서 차례)·크기·머리줄 글. 에이전트가 고칠 표를 짚는 데 쓴다."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = []
    for n in sorted(x for x in zin.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
        for m in _TBL.finditer(zin.read(n).decode("utf-8")):
            cells = _cells(m.group(0))
            if not cells:
                continue
            first = [c["text"] for c in sorted(cells, key=lambda c: (c["r"], c["c"])) if c["r"] == 0]
            out.append({"no": len(out) + 1, "rows": max(c["r"] + c["rs"] for c in cells),
                        "cols": max(c["c"] + c["cs"] for c in cells), "head": " | ".join(first)[:80]})
    return out


def apply(data: bytes, which: list[int] | None = None, opts: dict | None = None, only_paras: set[str] | None = None) -> bytes:
    """표에 서식 입히기 — which=표 번호(tables 의 no, 없으면 전부), only_paras=이 문단 id 들 안의 표만(AURA 가 방금 넣은 표)."""
    count = [0]

    def fn(xml: str, hdr: _Header, _n: str) -> str:
        spans = []
        if only_paras:
            for pid in only_paras:
                m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
                if m:
                    spans.append(m.start())

        def sub(m: re.Match) -> str:
            if not _cells(m.group(0)):
                return m.group(0)
            count[0] += 1
            if which and count[0] not in which:
                return m.group(0)
            if only_paras and not any(s <= m.start() and m.start() - s < 400 for s in spans):
                return m.group(0)
            return style_table(m.group(0), hdr, opts)
        return _TBL.sub(sub, xml)
    return _rewrite(data, fn)


DRESS = {"cover": {"pt": 18, "bold": True, "align": "CENTER"}, "cover_sub": {"pt": 12, "align": "CENTER"},
         "h1": {"pt": 14, "bold": True}, "h2": {"pt": 12, "bold": True}, "h3": {"pt": 11, "bold": True}}


def dress(data: bytes, plan: list[tuple[str, str]]) -> bytes:
    """표 밖 문단에 글 갈래별 모양(표지 제목·부제·절 제목 단계) — plan 은 문서 차례의 (글, 갈래). 글이 같은 문단을 차례로 짚는다."""
    want = [(re.sub(r"\s+", " ", t).strip(), DRESS[k]) for t, k in plan if t.strip() and k in DRESS]

    def fn(xml: str, hdr: _Header, _n: str) -> str:
        spans = [(m.start(), m.end()) for m in re.finditer(r"<hp:tbl\b.*?</hp:tbl>", xml, re.S)]
        out, pos, i = [], 0, 0
        for m in re.finditer(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", xml, re.S):
            if i >= len(want):
                break
            if any(a <= m.start() < b for a, b in spans):
                continue
            if _text(m.group(0)) != want[i][0]:
                continue
            st = want[i][1]
            i += 1
            x = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")',
                       lambda r: f'{r.group(1)}{hdr.char(r.group(2), bold=st.get("bold"), pt=st.get("pt"))}{r.group(3)}', m.group(0))
            if st.get("align"):
                x = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")', lambda r: f'{r.group(1)}{hdr.para(r.group(2), st["align"])}{r.group(3)}', x, count=1)
            out.append(xml[pos:m.start()] + x)
            pos = m.end()
        out.append(xml[pos:])
        return "".join(out)
    return _rewrite(data, fn)


# ── 도식 ── 학교 문서의 추진체계도·흐름도는 대부분 표 칸 상자 + 화살표로 그린다(그리기 개체보다 편집·이동이 쉽고 기안기에서 그대로 고친다).
# 에이전트는 본문에 「```도식」 블록으로 줄마다 「상자 → 상자 → 상자」 를 쓰고, 줄 사이는 아래 화살표(↓)로 잇는다. 상자 하나뿐인 줄은 가로로 꽉.
DIAGRAM_FILLS = ("#DFEAF5", "#F2F2F2")          # 첫 줄(상위·출발) 상자는 연파랑, 나머지는 연회색 — 채굴한 머리줄 색 계열


def diagram_lines(text: str) -> list[list[str]]:
    out = []
    for ln in str(text or "").split("\n"):
        boxes = [b.strip() for b in re.split(r"\s*(?:→|->|⇒|=>)\s*", ln.strip()) if b.strip()]
        if boxes and not re.fullmatch(r"[↓|]+", ln.strip()):
            out.append(boxes)
    return out[:12]


def diagram_table(lines: list[list[str]], hdr: _Header, pp: str, cp: str, width: int, table_id: int) -> str:
    """상자 줄 목록 → 표 XML(병합 칸·화살표 칸 테두리 없음). 열 = 상자 칸·화살표 칸 번갈아(2n-1)."""
    n = max(len(b) for b in lines)
    cols = max(2 * n - 1, 1)
    weights = [4 if c % 2 == 0 else 1 for c in range(cols)]
    col_w = [max(int(width * w / sum(weights)), 600) for w in weights]
    none_bf = hdr.border("", "", "", "", "none")
    pc = hdr.para(pp, "CENTER")
    bold = hdr.char(cp, bold=True)
    rows, r = [], 0

    def cell(text, c, r, span, bf, char, h=1800):
        w = sum(col_w[c:c + span])
        body = f'<hp:p id="0" paraPrIDRef="{pc}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{char}"><hp:t>{escape(text)}</hp:t></hp:run></hp:p>'
        return (f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{bf}">'
                f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
                f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{body}</hp:subList>'
                f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{span}" rowSpan="1"/>'
                f'<hp:cellSz width="{w}" height="{h}"/><hp:cellMargin left="283" right="283" top="283" bottom="283"/></hp:tc>')
    heights = 0
    for i, boxes in enumerate(lines):
        if i:                                                # 줄 사이 아래 화살표
            rows.append("<hp:tr>" + cell("↓", 0, r, cols, none_bf, cp, 900) + "</hp:tr>")
            r += 1
            heights += 900
        fill = DIAGRAM_FILLS[0] if i == 0 else DIAGRAM_FILLS[1]
        box_bf = hdr.border("0.12 mm", "0.12 mm", "0.12 mm", "0.12 mm", fill)
        tcs = []
        if len(boxes) == 1:
            tcs.append(cell(boxes[0], 0, r, cols, box_bf, bold))
        else:
            # 상자 k 개를 n 칸 가운데에 — 남는 칸은 양옆 빈 칸(테두리 없음)으로
            k = len(boxes)
            lead = (n - k)                                   # 화살표 칸 포함 앞쪽 빈 열 수(상자 칸 단위로 반씩)
            c = 0
            if lead:
                tcs.append(cell("", 0, r, lead, none_bf, cp))
                c = lead
            for j, b in enumerate(boxes):
                tcs.append(cell(b, c, r, 1, box_bf, bold if i == 0 else cp))
                c += 1
                if j < k - 1:
                    tcs.append(cell("→", c, r, 1, none_bf, cp))
                    c += 1
            if c < cols:
                tcs.append(cell("", c, r, cols - c, none_bf, cp))
        rows.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
        r += 1
        heights += 1800
    tbl = (f'<hp:tbl id="{table_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
           f'pageBreak="CELL" repeatHeader="0" rowCnt="{r}" colCnt="{cols}" cellSpacing="0" borderFillIDRef="{none_bf}" noAdjust="0">'
           f'<hp:sz width="{sum(col_w)}" widthRelTo="ABSOLUTE" height="{heights}" heightRelTo="ABSOLUTE" protect="0"/>'
           f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" '
           f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
           f'<hp:outMargin left="0" right="0" top="283" bottom="283"/><hp:inMargin left="141" right="141" top="141" bottom="141"/>'
           + "".join(rows) + "</hp:tbl>")
    return (f'<hp:p id="0" paraPrIDRef="{pc}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{cp}">{tbl}</hp:run><hp:run charPrIDRef="{cp}"><hp:t/></hp:run></hp:p>')


def place_diagrams(data: bytes, diagrams: list[str], width: int) -> bytes:
    """write_section 이 남긴 표시 문단(⟦도식n⟧)을 도식 표로 바꾼다 — 문단 id 는 그대로(다시 쓰면 지울 수 있게)."""
    def fn(xml: str, hdr: _Header, _n: str) -> str:
        def sub(m: re.Match) -> str:
            p = m.group(0)
            k = re.search(r"⟦도식(\d+)⟧", p)
            if not k or int(k.group(1)) >= len(diagrams):
                return p
            lines = diagram_lines(diagrams[int(k.group(1))])
            if not lines:
                return ""
            pid = re.search(r'\bid="(\d+)"', p).group(1)
            pp = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
            cp = re.search(r'charPrIDRef="(\d+)"', p).group(1)
            tid = int(pid) + 7
            return diagram_table(lines, hdr, pp, cp, width, tid).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1)
        return re.sub(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?⟦도식\d+⟧(?:(?!<hp:p\b).)*?</hp:p>", sub, xml, flags=re.S)
    return _rewrite(data, fn)
