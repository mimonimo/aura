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
        return max([int(x) for x in re.findall(rf'<hh:{tag} id="(\d+)"', self.xml)] or [0]) + 1

    def _append(self, group: str, body: str) -> None:
        close = self.xml.find(f"</hh:{group}>")
        self.xml = self.xml[:close] + body + self.xml[close:]
        m = re.search(rf'(<hh:{group}\b[^>]*\bitemCnt=")(\d+)(")', self.xml)
        if m:
            self.xml = self.xml[:m.start(2)] + str(int(m.group(2)) + 1) + self.xml[m.end(2):]

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

    def char(self, base: str, *, bold: bool | None = None, pt: float | None = None) -> str:
        key = ("cp", base, bold, pt)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:charPr id="{base}"[^>]*>.*?</hh:charPr>', self.xml, re.S)
        if not m:
            return base
        body = m.group(0)
        i = str(self._next_id("charPr"))
        body = re.sub(r'id="\d+"', f'id="{i}"', body, count=1)
        if pt:
            body = re.sub(r'\bheight="\d+"', f'height="{int(round(pt * 100))}"', body, count=1)
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
        m = re.search(rf'<hh:paraPr id="{base}"[^>]*>.*?</hh:paraPr>', self.xml, re.S)
        if not m or not align:
            return base
        body = m.group(0)
        if f'horizontal="{align}"' in body:
            self.cache[key] = base
            return base
        i = str(self._next_id("paraPr"))
        body = re.sub(r'id="\d+"', f'id="{i}"', body, count=1)
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
        is_label = bool(c["text"]) and not c["field"] and len(c["text"]) <= 20 and (
            (right is not None and (not right["text"] or right["field"]))
            or (c["c"] == 0 and not head_rows and ncols >= 2))
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
        if role == "box":
            bf = hdr.border(o["inner"], o["inner"], o["inner"], o["inner"], "none", "DASH")
            align = "JUSTIFY"
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
        x = re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{bf}", x, count=1)
        bold = bool(o["head_bold"]) if role == "head" else None
        pt = float(o["font_pt"]) if o.get("font_pt") else None

        def run(m: re.Match) -> str:
            return f'{m.group(1)}{hdr.char(m.group(2), bold=bold, pt=pt)}{m.group(3)}'
        x = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")', run, x)
        if align:
            x = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")', lambda m: f"{m.group(1)}{hdr.para(m.group(2), align)}{m.group(3)}", x)
        out.append(tbl[pos:c["s"]] + x)
        pos = c["e"]
    out.append(tbl[pos:])
    t = "".join(out)
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
