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

from zzaimy.export import house_style as hs
from zzaimy.export.house_style import DIAGRAM_FILLS, HOUSE, diagram_lines  # noqa: F401 — 기준값은 공용 모듈 한 곳(독스와 같이 씀)

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


_short = hs.short_value          # 가운데에 둘 값 — 기호·숫자·금액·날짜, 여섯 글자 이하


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
    """header.xml 에 테두리·글자·문단 모양을 더한다. 같은 모양이 이미 있으면(새로 만든 것이든 양식에 원래 있던 것이든) 그 id 를 다시 쓴다 —
    같은 서식을 두 번 입혀도 header 가 늘지 않고 결과가 같다(멱등)."""

    GROUP_ITEM = {"borderFills": "borderFill", "charProperties": "charPr", "paraProperties": "paraPr"}

    def __init__(self, xml: str):
        self.xml = xml
        self.cache: dict[tuple, str] = {}
        self._index: dict[str, dict[str, str]] = {}

    def _next_id(self, tag: str) -> int:
        return max([int(x) for x in re.findall(rf'<hh:{tag}\b[^>]*?\bid="(\d+)"', self.xml)] or [0]) + 1

    @staticmethod
    def _norm(body: str) -> str:
        return re.sub(r'\bid="\d+"', "", body, count=1)

    def _items(self, group: str) -> dict[str, str]:
        """모양 몸통(id 뺀 것) → id. 처음 부를 때 한 번 읽고 더할 때마다 고친다."""
        if group not in self._index:
            item = self.GROUP_ITEM[group]
            blk = re.search(rf"<hh:{group}\b.*?</hh:{group}>", self.xml, re.S)
            idx: dict[str, str] = {}
            for m in re.finditer(rf'<hh:{item}\b[^>]*?\bid="(\d+)"[^>]*>.*?</hh:{item}>', blk.group(0) if blk else "", re.S):
                idx.setdefault(self._norm(m.group(0)), m.group(1))
            self._index[group] = idx
        return self._index[group]

    def _append(self, group: str, body: str) -> str:
        """모양 하나를 더한다 — 같은 몸통이 있으면 더하지 않고 그 id. 돌려주는 것은 쓸 id."""
        key = self._norm(body)
        have = self._items(group).get(key)
        if have is not None:
            return have
        close = self.xml.find(f"</hh:{group}>")
        if close < 0:
            raise ValueError(f"header 에 {group} 목록이 없다")
        self.xml = self.xml[:close] + body + self.xml[close:]
        item = self.GROUP_ITEM[group]
        blk = re.search(rf"<hh:{group}\b.*?</hh:{group}>", self.xml, re.S)
        n = len(re.findall(rf"<hh:{item}\b", blk.group(0)))            # 실제 항목 수로(앞선 도구가 틀리게 적은 수도 바로잡힘)
        m = re.search(rf'(<hh:{group}\b[^>]*\bitemCnt=")(\d+)(")', self.xml)
        if m:
            self.xml = self.xml[:m.start(2)] + str(n) + self.xml[m.end(2):]
        new_id = re.search(r'\bid="(\d+)"', body).group(1)
        self._items(group)[key] = new_id
        return new_id

    def border(self, left: str, right: str, top: str, bottom: str, fill: str, kind: str = "SOLID",
               bottom_kind: str = "", color: str = "#000000", top_kind: str = "") -> str:
        key = ("bf", left, right, top, bottom, fill, kind, bottom_kind, color, top_kind)
        if key in self.cache:
            return self.cache[key]
        i = str(self._next_id("borderFill"))

        def side(name: str, w: str, k: str) -> str:
            return f'<hh:{name} type="{k if w else "NONE"}" width="{w or "0.1 mm"}" color="{color}"/>'
        brush = (f'<hc:fillBrush><hc:winBrush faceColor="{fill}" hatchColor="#999999" alpha="0"/></hc:fillBrush>'
                 if fill and fill != "none" else "")
        body = (f'<hh:borderFill id="{i}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
                '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
                + side("leftBorder", left, kind) + side("rightBorder", right, kind) + side("topBorder", top, top_kind or kind)
                + side("bottomBorder", bottom, bottom_kind or kind)
                + '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>' + brush + "</hh:borderFill>")
        i = self._append("borderFills", body)
        self.cache[key] = i
        return i

    def char(self, base: str, *, bold: bool | None = None, pt: float | None = None, color: str = "",
             spacing: int | None = None) -> str:
        """글자 모양 복제 — 굵게·크기(pt)·색·자간(%)."""
        key = ("cp", base, bold, pt, color, spacing)
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
        if spacing is not None:
            body = re.sub(r'(<hh:spacing\b)[^>]*?(/?>)', lambda s_: s_.group(1) + "".join(
                f' {k}="{int(spacing)}"' for k in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")) + s_.group(2),
                body, count=1)
        if bold is True and "<hh:bold/>" not in body:
            body = body.replace("<hh:underline", "<hh:bold/><hh:underline", 1) if "<hh:underline" in body \
                else body.replace("</hh:charPr>", "<hh:bold/></hh:charPr>", 1)
        elif bold is False:
            body = body.replace("<hh:bold/>", "")
        i = self._append("charProperties", body)
        self.cache[key] = i
        return i

    def para(self, base: str, align: str) -> str:
        return self.para_style(base, align=align)

    def para_style(self, base: str, *, align: str = "", line: int | None = None, before: float | None = None,
                   after: float | None = None, border: str = "", border_offset: int = 0, keep_next: bool | None = None) -> str:
        """문단 모양 복제 — 정렬, 줄 간격(%), 위·아래 간격(pt), 문단 테두리(border = borderFill id, 아래 선 같은 장식), 다음 문단과 함께.
        간격은 한글 규칙대로 기본(default) 판에 HWPUNIT 두 배 값, 글자 단위(case) 판에 그 반을 쓴다(webhwp_write._LevelStyles 와 같다)."""
        key = ("pp", base, align, line, before, after, border, border_offset, keep_next)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:paraPr\b[^>]*?\bid="{base}"[^>]*>.*?</hh:paraPr>', self.xml, re.S)
        if not m:
            return base
        body0 = body = m.group(0)
        if align:
            body = re.sub(r'(<hh:align horizontal=")[A-Z_]+', rf"\g<1>{align}", body, count=1)

        def fix(block: str, div: int) -> str:
            if line is not None:
                block = re.sub(r'(<hh:lineSpacing type=")[A-Z_]+(" value=")-?\d+', rf"\g<1>PERCENT\g<2>{int(line)}", block, count=1)
            if before is not None:
                block = re.sub(r'(<hc:prev value=")-?\d+', rf"\g<1>{int(round(before * 200)) // div}", block, count=1)
            if after is not None:
                block = re.sub(r'(<hc:next value=")-?\d+', rf"\g<1>{int(round(after * 200)) // div}", block, count=1)
            return block
        case = re.search(r"<hp:case\b.*?</hp:case>", body, re.S)
        if case:
            body = body[:case.start()] + fix(case.group(0), 2) + body[case.end():]
        dflt = re.search(r"<hp:default>.*?</hp:default>", body, re.S)
        if dflt:
            body = body[:dflt.start()] + fix(dflt.group(0), 1) + body[dflt.end():]
        if not case and not dflt:
            body = fix(body, 1)
        if border:
            if "<hh:border " in body:
                body = re.sub(r'<hh:border\b[^>]*/>', f'<hh:border borderFillIDRef="{border}" offsetLeft="0" offsetRight="0" '
                              f'offsetTop="{border_offset}" offsetBottom="{border_offset}" connect="0" ignoreMargin="0"/>', body, count=1)
            else:
                body = body.replace("</hh:paraPr>", f'<hh:border borderFillIDRef="{border}" offsetLeft="0" offsetRight="0" '
                                    f'offsetTop="{border_offset}" offsetBottom="{border_offset}" connect="0" ignoreMargin="0"/></hh:paraPr>', 1)
        if keep_next is not None:
            body = re.sub(r'(\bkeepWithNext=")\d', rf"\g<1>{1 if keep_next else 0}", body, count=1)
        if body == body0:
            self.cache[key] = base
            return base
        body = re.sub(r'\bid="\d+"', f'id="{self._next_id("paraPr")}"', body, count=1)
        i = self._append("paraProperties", body)
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
    return hs.cell_roles(_cells(tbl))


def _head_texts(cells: list[dict], rl: dict) -> dict[int, str]:
    """열 → 그 열을 덮는 머리줄 글(여러 줄이면 이어서) — 값 칸 정렬(금액 칸 오른쪽)의 단서."""
    out: dict[int, str] = {}
    for c in sorted(cells, key=lambda c: (c["r"], c["c"])):
        if rl.get((c["r"], c["c"])) != "head":
            continue
        for j in range(c["c"], c["c"] + c["cs"]):
            out[j] = (out.get(j, "") + " " + c["text"]).strip()
    return out


def style_table(tbl: str, hdr: _Header, opts: dict | None = None) -> str:
    """표 하나에 서식을 입힌다 — opts 는 HOUSE·TABLE 을 덮는 값.
    HOUSE: head_fill·label_fill·outer·inner·head_bottom(SOLID 굵게|DOUBLE 이중선|THIN 안쪽 선과 같게)·font_pt·head_bold·head_align·body_align.
    TABLE: total_fill·zebra·num_align. 그 밖: keep_borders(양식 선·음영은 두고 글·정렬·여백만), row_height(행 높이를 이 값으로 — 새로 만든 표),
    marks([[행, 열]…] 0부터 — 일정표의 추진 기간 칸 음영), line(칸 글 줄 간격 %).
    칸 쓰임: 머리줄·라벨(house_style.cell_roles), 합계 줄(첫 칸이 합계·계·소계), 금액 칸(머리가 금액·원 단위이거나 큰 수)."""
    o = {**HOUSE, **{k: hs.TABLE[k] for k in ("total_fill", "zebra", "zebra_fill", "num_align")}, **(opts or {})}
    o["outer"], o["inner"] = _width(o["outer"]), _width(o["inner"])
    cells = _cells(tbl)
    if not cells:
        return tbl
    nrows = max(c["r"] + c["rs"] for c in cells)
    ncols = max(c["c"] + c["cs"] for c in cells)
    rl = roles(tbl)
    if o.get("head_rows") is not None:                  # 빌더가 아는 머리줄 수 — 짐작(첫 행이 모두 글)보다 앞선다
        hr = int(o["head_rows"])
        rl = {k: ("head" if k[0] < hr else ("body" if v == "head" else v)) for k, v in rl.items()}
    if o.get("labels_only"):                            # 빌더 표 — 라벨은 준 칸만(빈 값 칸 옆 글을 라벨로 짐작하지 않는다)
        rl = {k: ("body" if v == "label" else v) for k, v in rl.items()}
    for r, c in o.get("labels") or []:                  # 빌더가 아는 라벨 칸(요약 표의 라벨 열) — 값이 차 있어도 라벨
        if (int(r), int(c)) in rl and rl[(int(r), int(c))] != "head":
            rl[(int(r), int(c))] = "label"
    head_last = max([c["r"] + c["rs"] for c in cells if rl.get((c["r"], c["c"])) == "head"] or [0])
    heads = _head_texts(cells, rl)
    first_text = {c["r"]: c["text"] for c in cells if c["c"] == 0}
    totals = {r for r, t in first_text.items() if r >= head_last and hs.is_total(t)} if ncols > 1 else set()
    marks = {(int(r), int(c)) for r, c in (o.get("marks") or [])}
    rule = str(o.get("head_bottom") or "SOLID").upper()
    out, pos = [], 0
    for c in cells:
        role = rl.get((c["r"], c["c"]), "body")
        x = c["xml"]
        total = c["r"] in totals
        if role == "box":                                   # 작성 안내 상자 — 점선, 작은 회색 글, 왼쪽 정렬(본문과 구별)
            bf = hdr.border(o["inner"], o["inner"], o["inner"], o["inner"], "none", hs.BOX["dash"])
            align = hs.BOX["align"]
            if o.get("row_height"):
                x = re.sub(r'(<hp:cellSz width="\d+" height=")(\d+)"', lambda m: f'{m.group(1)}{int(o["row_height"])}"', x, count=1)
        else:
            left = o["outer"] if c["c"] == 0 else o["inner"]
            right = o["outer"] if c["c"] + c["cs"] >= ncols else o["inner"]
            top = o["outer"] if c["r"] == 0 else o["inner"]
            bottom = o["outer"] if c["r"] + c["rs"] >= nrows else o["inner"]
            fill = o["head_fill"] if role == "head" else o["label_fill"] if role == "label" else "none"
            if total and role != "head":
                fill = o["total_fill"]
            elif (c["r"], c["c"]) in marks:
                fill = hs.TABLE["month_fill"]
            elif o.get("zebra") and role == "body" and (c["r"] - head_last) % 2 == 1:
                fill = o["zebra_fill"]
            bk = ""
            # 머리줄 아래 선 — 선 굵기 위계(바깥 > 머리줄 아래 > 안쪽). 바로 아래 행의 위 선도 같게(두 칸 선이 겹쳐 그려진다)
            if head_last and c["r"] + c["rs"] == head_last and role == "head" and c["r"] + c["rs"] < nrows:
                if rule == "DOUBLE":
                    bottom, bk = "0.5 mm", "DOUBLE_SLIM"
                elif rule != "THIN":
                    bottom = _width(hs.TABLE["head_rule_width"])
            tk = ""
            if head_last and c["r"] == head_last and role != "head":
                if rule == "DOUBLE":
                    top, tk = "0.5 mm", "DOUBLE_SLIM"
                elif rule != "THIN":
                    top = _width(hs.TABLE["head_rule_width"])
            if total and c["r"] > 0:                          # 합계 줄 위 선도 머리줄 아래 선 굵기
                top = _width(hs.TABLE["total_rule_width"])
            bf = hdr.border(left, right, top, bottom, _color(fill), "SOLID", bk, top_kind=tk)
            align = o["head_align"] if role in ("head", "label") else o.get("body_align") or ""
            if total and role != "head" and c["c"] == 0:
                align = "CENTER"
            if role == "body" and not align:
                # 금액·큰 수는 오른쪽, 기호·짧은 값은 가운데(학교 표의 거의 모든 값 칸), 긴 글은 문단 기본
                align = hs.value_align(c["text"], heads.get(c["c"], "")) or ("CENTER" if (c["r"], c["c"]) in marks else "")
                if not align and c["field"]:                  # 쓸 자리(누름틀) 칸 — 양쪽 정렬이면 좁은 칸의 안내 글이 벌어진다
                    align = "LEFT"
            # 칸 여백 — 학교 표의 절반이 510/510/141/141(좌우 1.8mm). 좁으면 글이 선에 붙는다
            x = re.sub(r'(<hp:tc\b[^>]*\bhasMargin=")0', r"\g<1>1", x, count=1)
            m_ = hs.CELL_MARGIN
            x = re.sub(r'<hp:cellMargin [^>]*/>', f'<hp:cellMargin left="{m_["left"]}" right="{m_["right"]}" top="{m_["top"]}" bottom="{m_["bottom"]}"/>', x, count=1)
            # 줄 높이 — 10pt 한 줄 + 여백이 들어가게 최소 1,900(약 6.7mm). 1,200 이면 글이 위아래 선에 닿는다. row_height 를 주면 그 값(새 표)
            if o.get("row_height"):
                tall = {int(k): int(v) for k, v in (o.get("tall") or {}).items()}
                h_ = sum(tall.get(r, int(o["row_height"])) for r in range(c["r"], c["r"] + c["rs"]))
                x = re.sub(r'(<hp:cellSz width="\d+" height=")(\d+)"', lambda m: f'{m.group(1)}{h_}"', x, count=1)
            else:
                x = re.sub(r'(<hp:cellSz width="\d+" height=")(\d+)"', lambda m: f'{m.group(1)}{max(int(m.group(2)), hs.MIN_ROW_HEIGHT)}"', x, count=1)
            # 기안기는 「제목 칸」(header="1")의 글자 굵기를 무시한다 — 머리줄 굵게가 보이도록 끈다
            x = re.sub(r'(<hp:tc\b[^>]*\bheader=")1', r"\g<1>0", x, count=1)
            x = re.sub(r'(<hp:subList\b[^>]*\bvertAlign=")\w+', r"\g<1>CENTER", x, count=1)
        if not o.get("keep_borders"):                        # 양식 표 모양을 따른 표는 선·음영을 그대로 두고 글·정렬·여백만
            x = re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{bf}", x, count=1)
        bold = bool(o["head_bold"]) if role == "head" else (True if total and hs.TABLE["total_bold"] else None)
        pt = float(o["font_pt"]) if o.get("font_pt") else None
        color = ""
        if role == "box":
            pt, color = (pt or HOUSE["font_pt"]) + hs.BOX["font_delta"], hs.BOX["color"]

        def run(m: re.Match) -> str:
            return f'{m.group(1)}{hdr.char(m.group(2), bold=bold, pt=pt, color=color)}{m.group(3)}'
        x = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")', run, x)
        line = o.get("line")
        if align or line:
            x = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")',
                       lambda m: f"{m.group(1)}{hdr.para_style(m.group(2), align=align, line=line)}{m.group(3)}", x)
        out.append(tbl[pos:c["s"]] + x)
        pos = c["e"]
    out.append(tbl[pos:])
    t = "".join(out)
    if o.get("row_height"):                                   # 표 높이 = 행 높이 합
        tall = {int(k): int(v) for k, v in (o.get("tall") or {}).items()}
        rows_h = sum(tall.get(r, int(o["row_height"])) for r in range(nrows))
        t = re.sub(r'(<hp:sz width="\d+" widthRelTo="\w+" height=")\d+', rf"\g<1>{rows_h}", t, count=1)
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


def apply(data: bytes, which: list[int] | None = None, opts: dict | None = None, only_paras: set[str] | None = None,
          per_table: dict[int, dict] | None = None) -> bytes:
    """표에 서식 입히기 — which=표 번호(tables 의 no, 없으면 전부), only_paras=이 문단 id 들 안의 표만(AURA 가 방금 넣은 표),
    per_table={표 번호: 그 표만 덮을 값}(양식 빌더가 표 갈래마다 아는 것 — 일정표 음영 칸, 라벨 칸, 행 높이)."""
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
            return style_table(m.group(0), hdr, {**(opts or {}), **((per_table or {}).get(count[0]) or {})})
        return _TBL.sub(sub, xml)
    return _rewrite(data, fn)


# 글 갈래별 모양은 house_style.TYPE 한 곳 — 옛 이름(cover)은 표지 제목
DRESS = {"cover": "cover_title"}


def type_ids(hdr: _Header, kind: str, cp: str, pp: str) -> tuple[str, str]:
    """TYPE 갈래 하나 → (글자 모양 id, 문단 모양 id). 아래 선(rule)·위 선(rule_top)은 남색 문단 테두리로, 제목은 다음 문단과 같은 쪽에."""
    st = hs.TYPE[DRESS.get(kind, kind)]
    c = hdr.char(cp, bold=st.get("bold"), pt=st.get("pt"), color=st.get("color", ""), spacing=st.get("spacing"))
    border = ""
    if st.get("rule") or st.get("rule_top"):
        border = hdr.border("", "", _width(st["rule_top"]) if st.get("rule_top") else "", _width(st["rule"]) if st.get("rule") else "",
                            "none", color=hs.PALETTE["accent"])
    heading = kind in ("chapter", "h1", "h2", "h3", "toc_title")
    p = hdr.para_style(pp, align=st.get("align", ""), line=st.get("line"), before=st.get("before"), after=st.get("after"),
                       border=border, border_offset=0, keep_next=True if heading else None)
    return c, p


def dress(data: bytes, plan: list[tuple[str, str]]) -> bytes:
    """표 밖 문단에 글 갈래별 모양(house_style.TYPE — 표지 제목·부제·날짜·기관, 목차, 장·1·2·3단계 제목) — plan 은 문서 차례의 (글, 갈래).
    글이 같은 문단을 차례로 짚는다. 갈래 「body」 는 쓸 자리 문단이 아니라 바탕글 글자 모양 자체를 바꾼다(body_base)."""
    want = [(re.sub(r"\s+", " ", t).strip(), DRESS.get(k, k)) for t, k in plan if t.strip() and DRESS.get(k, k) in hs.TYPE]

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
            kind = want[i][1]
            i += 1
            p0 = m.group(0)
            cp = re.search(r'charPrIDRef="(\d+)"', p0)
            pp = re.search(r'paraPrIDRef="(\d+)"', p0)
            if not cp or not pp:
                continue
            c_id, p_id = type_ids(hdr, kind, cp.group(1), pp.group(1))
            x = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")', rf"\g<1>{c_id}\3", p0)
            x = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")', rf"\g<1>{p_id}\3", x, count=1)
            x = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", x, flags=re.S)   # 모양을 바꾼 문단만 줄 배치를 다시
            out.append(xml[pos:m.start()] + x)
            pos = m.end()
        out.append(xml[pos:])
        return "".join(out)
    return _rewrite(data, fn)


def body_base(data: bytes) -> bytes:
    """바탕글(스타일 0)의 글자 크기·문단 줄 간격을 TYPE body 로 — 새로 만드는 양식에만 쓴다(본문·쓸 자리 문단이 모두 이 모양을 잇는다)."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    h = zin.read("Contents/header.xml").decode("utf-8")
    st = re.search(r'<hh:style\b[^>]*\bid="0"[^>]*>', h)
    if not st:
        return data
    cp = re.search(r'charPrIDRef="(\d+)"', st.group(0)).group(1)
    b = hs.TYPE["body"]
    h2 = re.sub(rf'(<hh:charPr\b[^>]*?\bid="{cp}"[^>]*?\bheight=")\d+', rf"\g<1>{int(b['pt'] * 100)}", h, count=1)
    if h2 == h:
        return data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = h2.encode("utf-8") if info.filename == "Contents/header.xml" else zin.read(info.filename)
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


# ── 도식 ── 학교 문서의 추진체계도·흐름도는 대부분 표 칸 상자 + 화살표로 그린다(그리기 개체보다 편집·이동이 쉽고 기안기에서 그대로 고친다).
# 에이전트는 본문에 「```도식」 블록으로 줄마다 「상자 → 상자 → 상자」 를 쓰고, 줄 사이는 아래 화살표(↓)로 잇는다. 상자 하나뿐인 줄은 가로로 꽉.
# 상자 색·선·높이·여백·줄 나누기(diagram_lines)는 house_style 의 값 — 독스 도식과 같은 꼴


def diagram_table(lines: list[list[str]], hdr: _Header, pp: str, cp: str, width: int, table_id: int) -> str:
    """상자 줄 목록 → 표 XML(병합 칸·화살표 칸 테두리 없음). 열 = 상자 칸·화살표 칸 번갈아(2n-1)."""
    n = max(len(b) for b in lines)
    cols = max(2 * n - 1, 1)
    weights = [hs.DIAGRAM_WEIGHTS[0] if c % 2 == 0 else hs.DIAGRAM_WEIGHTS[1] for c in range(cols)]
    col_w = [max(int(width * w / sum(weights)), 600) for w in weights]
    none_bf = hdr.border("", "", "", "", "none")
    pc = hdr.para(pp, "CENTER")
    bold = hdr.char(cp, bold=True)
    rows, r = [], 0

    def cell(text, c, r, span, bf, char, h=hs.DIAGRAM_BOX_HEIGHT):
        w = sum(col_w[c:c + span])
        body = f'<hp:p id="0" paraPrIDRef="{pc}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{char}"><hp:t>{escape(text)}</hp:t></hp:run></hp:p>'
        return (f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{bf}">'
                f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
                f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{body}</hp:subList>'
                f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{span}" rowSpan="1"/>'
                f'<hp:cellSz width="{w}" height="{h}"/><hp:cellMargin left="{dm}" right="{dm}" top="{dm}" bottom="{dm}"/></hp:tc>')
    dm = hs.DIAGRAM_MARGIN
    heights = 0
    for i, boxes in enumerate(lines):
        if i:                                                # 줄 사이 아래 화살표
            rows.append("<hp:tr>" + cell("↓", 0, r, cols, none_bf, cp, hs.DIAGRAM_ARROW_HEIGHT) + "</hp:tr>")
            r += 1
            heights += hs.DIAGRAM_ARROW_HEIGHT
        fill = DIAGRAM_FILLS[0] if i == 0 else DIAGRAM_FILLS[1]
        box_bf = hdr.border(*[hs.DIAGRAM_BORDER] * 4, fill)
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
        heights += hs.DIAGRAM_BOX_HEIGHT
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
