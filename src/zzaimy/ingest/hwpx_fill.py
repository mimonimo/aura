"""원본 한글 서식(hwpx)에 내용을 채운다 — 서식 보존 채우기(ADR-0035).

참조·협업은 구글독스 작업본에서 하고, 최종본은 원본 서식에 그 내용을 넣어 만든다(사용자 확정 2026-09-28). kordoc patch 는 블록 추가를
못 하므로(v1) 우리 hwpx 계층에서 직접: 절 제목 문단(또는 그 뒤의 【작성방법】 상자) 뒤에 문단·표를 끼워 넣는다.

kordoc(roundtrip/patcher·source-map·zip-patch·table-insert)에서 흡수한 원리 — 흐름이 아니라 기술만:
- 구역 XML 을 다시 직렬화하지 않고 글자열 위치에 끼워 넣는다(바이트 보존). 손대지 않은 문단은 한 글자도 안 바뀐다.
- 글이 바뀐 구역은 줄 배치 캐시(hp:linesegarray)를 전부 지운다 — 한글이 다시 계산하고, 변조 경고·옛 줄배치 렌더를 막는다.
- ZIP 은 원본 항목 순서·압축 방식을 그대로 두고 바뀐 항목만 다시 쓴다(mimetype 첫 항목·무압축 규약이 자동 보존).
- 새 표의 id 는 문서 전체 숫자 id 최댓값 다음부터(충돌 없음). 표 테두리는 그 구역 본문 표가 가장 많이 쓰는 실선 borderFill 을 승계하고,
  표가 하나도 없는 서식이면 실선 borderFill 하나를 header 에 추가(itemCnt 갱신)한다.

작업본은 서식의 변환본이라 서식 자체의 글·표도 들어 있다(첫 실측 2026-09-29: 그대로 넣으니 표 63→110 개로 겹침). 그래서 작업본 내용을
서식과 견줘 넣는다 — 일반 규칙이지 특정 서식 규칙이 아니다:
- 서식에 이미 있는 문단·표(글자 집합이 서식 표에 포함)는 다시 넣지 않고, 그 자리를 지나 뒤에 이어 쓴다(작업본 순서 유지).
- 서식 표를 작업본에서 고쳐 썼으면(칸 값이 달라짐) 서식 표의 칸에 그 값을 써 넣는다 — 표 서식(병합·테두리·열 폭)은 그대로.
  칸 대응은 행 수가 같고 행마다 칸 수가 같거나(순서대로) 작업본 행이 격자 열 수와 같을 때(colAddr 로). 못 맞추면 새 표로 넣는다.
- 작업본에서 에이전트가 만든 소제목(서식에 없는 '1) 강점(S)' 같은 것)은 바로 앞 절의 본문으로 이어 넣는다. 점 번호(1.1 같은) 제목이
  서식에 없으면 건너뛰고 보고한다(서식 구조가 다른 것이므로 사람이 본다).

원칙(특정 서식에 맞추지 않는다):
- 문단 서식은 문서의 기본 스타일('바탕글', style id 0)의 문단·글자 모양 참조를 빌린다 — 서식의 글꼴·크기·줄 간격이 그대로.
- 절 제목은 번호와 제목 글자로 찾는다(section_context 와 같은 잣대). 없는 절은 건너뛰고 보고한다.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, unescape

from zzaimy.app import section_context as sc

_T_ANY = re.compile(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SEC_RE = re.compile(r"Contents/section(\d+)\.xml$")
_LINESEG = re.compile(r"<(\w+:)?linesegarray\b[^>]*?(?:/>|>.*?</\1linesegarray>)", re.S)
_NUM_ID = re.compile(r"\bid(?:Ref)?=\"(\d{1,10})\"")
_BOX_RE = re.compile(r"【\s*(작성방법|증빙자료|작성\s*가이드|작성\s*요령)\s*】")
_DOTTED = re.compile(r"^\s*\d+(\.\d+)+\.?\s")
_NONWORD = re.compile(r"[^\w]+")


def _norm(s: str) -> str:
    """겹침 판정용 — 공백·기호·사설 글자(･ 의  같은 것)를 빼고 소문자."""
    return _NONWORD.sub("", s or "").lower()


def _top_level(xml: str, tag: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """[start, end) 안에서 <tag ...>…</tag> 의 최상위 범위 — 같은 태그가 안에 겹치면(표 안 표) 깊이로 건너뛴다."""
    end = len(xml) if end is None else end
    open_re = re.compile(rf"<{re.escape(tag)}\b[^>]*?(/?)>")
    close = f"</{tag}>"
    out: list[tuple[int, int]] = []
    pos = start
    while True:
        m = open_re.search(xml, pos, end)
        if not m:
            break
        if m.group(1) == "/":
            out.append((m.start(), m.end()))
            pos = m.end()
            continue
        depth = 1
        i = m.end()
        while depth and i < end:
            nxt = open_re.search(xml, i, end)
            c = xml.find(close, i, end)
            if c < 0:
                i = end
                break
            if nxt and nxt.start() < c:
                if nxt.group(1) != "/":
                    depth += 1
                i = nxt.end()
            else:
                depth -= 1
                i = c + len(close)
        out.append((m.start(), i))
        pos = i
    return out


def _top_level_paragraphs(xml: str) -> list[tuple[int, int]]:
    return _top_level(xml, "hp:p")


def _texts(fragment: str) -> list[str]:
    """hp:t 안의 글(탭 같은 안쪽 태그는 공백으로)."""
    return [" ".join(unescape(_TAG.sub(" ", m.group(1))).split()) for m in _T_ANY.finditer(fragment)]


def _para_text(block: str) -> str:
    """문단 글(표 안 글 제외) — 첫 표 태그 앞까지의 hp:t 만."""
    cut = block.find("<hp:tbl")
    head = block if cut < 0 else block[:cut]
    return " ".join(" ".join(_texts(head)).split())


_WALK = re.compile(r"<hp:p\b[^>]*>|</hp:p>|<hp:subList\b[^>]*>|</hp:subList>|<hp:tbl\b[^>]*>|</hp:tbl>|<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", re.S)


class _Known:
    """서식에 이미 있는 글 — 작업본 내용이 서식 것인지 볼 때. 어느 깊이든 모든 문단의 글과, 담는 단위(셀·글상자 subList·표·구역 본문)마다
    문단 차례를 둔다. 독스 변환본은 글상자의 여러 문단이나 목차 표의 여러 칸을 한 칸 글로 합치므로, 이어진 문단들을 붙인 것도 '있는 글'로 본다."""

    def __init__(self) -> None:
        self.paras: set[str] = set()
        self.runs: list[list[str]] = []
        self.joined: set[str] = set()

    def add_xml(self, xml: str) -> None:
        stack: list[list[str]] = []          # 문단 글 버퍼
        containers: list[list[str]] = [[]]   # subList·tbl 마다 문단 norm 차례(맨 아래는 구역 본문)
        for m in _WALK.finditer(xml):
            tok = m.group(0)
            if tok.startswith("</hp:p>"):
                if stack:
                    t = _norm(" ".join(stack.pop()))
                    if t:
                        self.paras.add(t)
                        containers[-1].append(t)
            elif tok.startswith("<hp:p"):
                stack.append([])
            elif tok.startswith("<hp:subList") or tok.startswith("<hp:tbl"):
                containers.append([])
            elif tok.startswith("</hp:subList>") or tok.startswith("</hp:tbl>"):
                run = containers.pop()
                if run:
                    self.runs.append(run)
                    self.joined.add("".join(run))
                    containers[-1].extend(run)       # 바깥 단위(표 전체·구역)에서도 이어 붙일 수 있게
            elif stack:
                stack[-1].append(unescape(_TAG.sub(" ", m.group(1))))
        if containers and containers[0]:
            self.runs.append(containers[0])

    def has(self, n: str) -> bool:
        """정규화한 글 n 이 서식에 있는가 — 문단 하나, 담는 단위 전체, 또는 한 단위 안의 이어진 문단들을 붙인 것."""
        if not n:
            return True
        if n in self.paras or n in self.joined:
            return True
        if len(n) < 8:
            return False
        for run in self.runs:
            for i, first in enumerate(run):
                if not first or not n.startswith(first):
                    continue
                acc = first
                j = i + 1
                while acc != n and j < len(run) and n.startswith(acc + run[j]):
                    acc += run[j]
                    j += 1
                if acc == n:
                    return True
        return False


def _without_tables(fragment: str) -> str:
    for a, b in sorted(_top_level(fragment, "hp:tbl"), reverse=True):
        fragment = fragment[:a] + fragment[b:]
    return fragment


@dataclass
class _Cell:
    tc: tuple[int, int]
    inner: tuple[int, int]            # subList 안쪽 범위(문단들)
    col: int
    text: str
    nested: bool
    p_open: str                       # 첫 문단 여는 태그(속성 승계용)
    char_ref: str
    full: str = ""                    # 안쪽 표까지 합친 글(대조용)


@dataclass
class _FormTable:
    para: tuple[int, int]             # 표를 담은 최상위 문단
    rows: list[list[_Cell]]
    col_cnt: int
    cells: set[str] = field(default_factory=set)
    consumed: bool = False


def _parse_table(xml: str, para: tuple[int, int]) -> _FormTable | None:
    tbls = _top_level(xml, "hp:tbl", para[0], para[1])
    if not tbls:
        return None
    ta, tb = tbls[0]
    open_tag = xml[ta:xml.index(">", ta) + 1]
    cc = re.search(r"colCnt=\"(\d+)\"", open_tag)
    rows: list[list[_Cell]] = []
    for ra, rb in _top_level(xml, "hp:tr", ta, tb):
        row: list[_Cell] = []
        for ca, cb in _top_level(xml, "hp:tc", ra, rb):
            subs = _top_level(xml, "hp:subList", ca, cb)
            if not subs:
                continue
            sa, sb = subs[0]
            ia = xml.index(">", sa) + 1
            ib = sb - len("</hp:subList>")
            inner = xml[ia:ib]
            addr = re.search(r"<hp:cellAddr\b[^>]*colAddr=\"(\d+)\"", xml[ca:cb])
            pm = re.search(r"<hp:p\b[^>]*>", inner)
            cm = re.search(r"charPrIDRef=\"(\d+)\"", inner)
            row.append(_Cell(tc=(ca, cb), inner=(ia, ib), col=int(addr.group(1)) if addr else len(row),
                             text=" ".join(" ".join(_texts(_without_tables(inner))).split()),
                             nested="<hp:tbl" in inner, p_open=pm.group(0) if pm else "", char_ref=cm.group(1) if cm else "0",
                             full=_norm(" ".join(_texts(inner)))))
        rows.append(row)
    t = _FormTable(para=para, rows=rows, col_cnt=int(cc.group(1)) if cc else max((len(r) for r in rows), default=0))
    t.cells = {n for r in rows for c in r for n in (_norm(c.text), c.full) if n}
    return t


def _default_refs(header_xml: str) -> tuple[str, str]:
    """기본 스타일('바탕글', id 0)의 paraPrIDRef·charPrIDRef."""
    m = re.search(r"<hh:style\b[^>]*\bid=\"0\"[^>]*>", header_xml)
    if not m:
        return "0", "0"
    tag = m.group(0)
    pp = re.search(r"paraPrIDRef=\"(\d+)\"", tag)
    cp = re.search(r"charPrIDRef=\"(\d+)\"", tag)
    return (pp.group(1) if pp else "0"), (cp.group(1) if cp else "0")


def _text_width_hu(section_xml: str) -> int:
    m = re.search(r"<hp:pagePr\b[^>]*width=\"(\d+)\"", section_xml)
    mm = re.search(r"<hp:margin\b[^>]*/>", section_xml)
    if not m:
        return 48000
    w = int(m.group(1))
    if mm:
        for key in ("left", "right", "gutter"):
            g = re.search(rf"\b{key}=\"(\d+)\"", mm.group(0))
            if g:
                w -= int(g.group(1))
    return max(w, 10000)


def _max_numeric_id(xmls: list[str]) -> int:
    """문서 전체의 숫자 id/idRef 최댓값 — 새 표 id 는 그 다음부터(kordoc collectMaxNumericId)."""
    best = -1
    for xml in xmls:
        for m in _NUM_ID.finditer(xml):
            best = max(best, int(m.group(1)))
    return best


def _solid_border_fill_xml(new_id: int) -> str:
    return (f'<hh:borderFill id="{new_id}" threeD="0" shadow="0" centerLine="0" breakCellSeparateLine="0">'
            '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
            '<hh:leftBorder type="SOLID" width="0.12 mm" color="#000000"/><hh:rightBorder type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hh:topBorder type="SOLID" width="0.12 mm" color="#000000"/><hh:bottomBorder type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hh:diagonal type="NONE" width="0.1 mm" color="#000000"/><hh:fillInfo/></hh:borderFill>')


def _inject_border_fill(header_xml: str) -> tuple[str, str]:
    """header 의 borderFills 에 실선 테두리 하나를 덧붙이고 (새 header, 그 id) 를 돌려준다. 표 없는 서식용."""
    open_m = re.search(r"<hh:borderFills\b([^>]*)>", header_xml)
    close = header_xml.find("</hh:borderFills>")
    if not open_m or close < 0:
        return header_xml, "1"
    ids = [int(x) for x in re.findall(r"<hh:borderFill\b[^>]*\bid=\"(\d+)\"", header_xml)]
    new_id = (max(ids) if ids else 0) + 1
    head = header_xml
    cnt = re.search(r"\bitemCnt=\"(\d+)\"", open_m.group(1))
    if cnt:
        a = open_m.start(1) + cnt.start(1)
        b = open_m.start(1) + cnt.end(1)
        head = head[:a] + str(int(cnt.group(1)) + 1) + head[b:]
        close += len(str(int(cnt.group(1)) + 1)) - len(cnt.group(1))
    head = head[:close] + _solid_border_fill_xml(new_id) + head[close:]
    return head, str(new_id)


def _solid_border_ids(header_xml: str) -> set[str]:
    """네 변이 모두 실선인 borderFill id — 본문 표의 테두리로 쓸 수 있는 것."""
    out: set[str] = set()
    for m in re.finditer(r"<hh:borderFill\b[^>]*\bid=\"(\d+)\"[^>]*>(.*?)</hh:borderFill>", header_xml, re.S):
        body = m.group(2)
        sides = [re.search(rf"<hh:{side}Border\b[^>]*type=\"([A-Z_]+)\"", body) for side in ("left", "right", "top", "bottom")]
        if all(x and x.group(1) == "SOLID" for x in sides):
            out.add(m.group(1))
    return out


def _table_template(section_xml: str, solid: set[str] | None = None) -> tuple[str | None, str]:
    """구역의 본문 표(안내 상자 제외)가 가장 많이 쓰는 실선 셀 borderFillIDRef 와 셀 여백 태그 — 없으면 (None, 기본 여백).
    안내 상자(【작성방법】)는 점선이라 본문 표의 본이 아니다."""
    refs: Counter = Counter()
    for a, b in _top_level_paragraphs(section_xml):
        block = section_xml[a:b]
        if "<hp:tbl" not in block or _BOX_RE.search(block):
            continue
        refs.update(re.findall(r"<hp:tc\b[^>]*borderFillIDRef=\"(\d+)\"", block))
    if solid:
        refs = Counter({k: v for k, v in refs.items() if k in solid})
    bf = refs.most_common(1)[0][0] if refs else None
    mar = re.search(r"<hp:cellMargin\b[^>]*/>", section_xml)
    margin = mar.group(0) if mar else '<hp:cellMargin left="141" right="141" top="141" bottom="141"/>'
    return bf, margin


def paragraph_xml(text: str, pp: str, cp: str) -> str:
    return (f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{cp}"><hp:t>{escape(text)}</hp:t></hp:run></hp:p>')


def table_xml(rows: list[list[str]], pp: str, cp: str, width_hu: int, bf: str, margin: str, table_id: int,
              widths: list[float] | None = None) -> str:
    """새 표 XML. widths(작업본 표의 열 너비, 단위 무관)가 있으면 그 비율로, 없으면 본문 폭을 균등 분할."""
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    if not n_rows or not n_cols:
        return ""
    if widths and len(widths) == n_cols and sum(widths) > 0:
        col_ws = [max(int(width_hu * w / sum(widths)), 1000) for w in widths]
    else:
        col_ws = [max(int(width_hu / n_cols), 1000)] * n_cols
    row_h = 1200
    trs = []
    for r, row in enumerate(rows):
        tcs = []
        for c in range(n_cols):
            cell = row[c] if c < len(row) else ""
            lines = [ln for ln in str(cell).split("\n")] or [""]
            paras = "".join(paragraph_xml(ln, pp, cp) for ln in lines)
            tcs.append(f'<hp:tc name="" header="{1 if r == 0 else 0}" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{bf}">'
                       f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
                       f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{paras}</hp:subList>'
                       f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
                       f'<hp:cellSz width="{col_ws[c]}" height="{row_h}"/>{margin}</hp:tc>')
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    total_w = sum(col_ws)
    tbl = (f'<hp:tbl id="{table_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
           f'pageBreak="CELL" repeatHeader="1" rowCnt="{n_rows}" colCnt="{n_cols}" cellSpacing="0" borderFillIDRef="{bf}" noAdjust="0">'
           f'<hp:sz width="{total_w}" widthRelTo="ABSOLUTE" height="{row_h * n_rows}" heightRelTo="ABSOLUTE" protect="0"/>'
           f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" '
           f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
           f'<hp:outMargin left="0" right="0" top="283" bottom="283"/><hp:inMargin left="141" right="141" top="141" bottom="141"/>'
           + "".join(trs) + "</hp:tbl>")
    return (f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{cp}">{tbl}</hp:run><hp:run charPrIDRef="{cp}"><hp:t/></hp:run></hp:p>')


def _heading_matches(text: str, heading: str) -> bool:
    num, title = sc.split_number(heading)
    tnum, ttitle = sc.split_number(text)
    if num and tnum:
        return num == tnum and (sc.title_like(ttitle, title) or not ttitle)
    if num and not tnum:
        return False
    return sc.title_like(text, heading) and abs(len(sc.norm(text)) - len(sc.norm(heading))) <= 6


def strip_linesegs(xml: str) -> tuple[str, int]:
    """줄 배치 캐시를 전부 지운다 — 글이 바뀐 구역에만 쓴다."""
    out, n = _LINESEG.subn("", xml)
    return out, n


def _write_patched(src: Path, out: Path, replaced: dict[str, bytes]) -> None:
    """원본 ZIP 의 항목 순서·압축 방식을 지키고 바뀐 항목만 새 내용으로 쓴다(kordoc zip-patch 의 원리)."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            data = replaced.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            ni.compress_type = info.compress_type if info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) else zipfile.ZIP_DEFLATED
            ni.external_attr = info.external_attr
            zout.writestr(ni, data)


def _remove_boxes(xml: str) -> tuple[str, int]:
    """【작성방법】·【증빙자료】 상자(표만 든 최상위 문단)를 지운다 — 제출본 마무리."""
    paras = _top_level_paragraphs(xml)
    drop = []
    for a, b in paras:
        block = xml[a:b]
        if "<hp:tbl" in block and _BOX_RE.search(block) and not _para_text(block).strip():
            drop.append((a, b))
    for a, b in sorted(drop, reverse=True):
        xml = xml[:a] + xml[b:]
    return xml, len(drop)


def _docs_cells(rows: list[list[str]]) -> set[str]:
    return {_norm(str(c)) for r in rows for c in r if _norm(str(c))}


def _map_cells(form: _FormTable, rows: list[list[str]]) -> list[tuple[_Cell, str]] | None:
    """작업본 표의 칸 → 서식 표의 칸. 행 수가 같고, 행마다 칸 수가 같거나(순서대로) 작업본 행이 격자 열 수와 같을 때(colAddr)."""
    if len(rows) != len(form.rows):
        return None
    pairs: list[tuple[_Cell, str]] = []
    for drow, frow in zip(rows, form.rows):
        if len(drow) == len(frow):
            pairs += [(fc, str(dc)) for fc, dc in zip(frow, drow)]
        elif len(drow) == form.col_cnt:
            by_col = {fc.col: fc for fc in frow}
            for c, dc in enumerate(drow):
                if c in by_col:
                    pairs.append((by_col[c], str(dc)))
                elif _norm(str(dc)):
                    return None                                  # 병합에 가려진 칸에 값이 있다 — 대응 불가
        else:
            return None
    return pairs


class _Section:
    def __init__(self, name: str, xml: str, solid: set[str]) -> None:
        self.name = name
        self.xml = xml
        self.paras = _top_level_paragraphs(xml)
        self.texts = [_para_text(xml[a:b]) for a, b in self.paras]
        self.tables: list[_FormTable] = []
        for p in self.paras:
            if "<hp:tbl" in xml[p[0]:p[1]]:
                t = _parse_table(xml, p)
                if t:
                    self.tables.append(t)
        self.width = _text_width_hu(xml)
        self.bf, self.margin = _table_template(xml, solid)
        self.splices: list[tuple[int, int, str]] = []


def fill(src: Path | str, bodies: list[dict], out: Path | str, remove_boxes: bool = False) -> dict:
    """bodies = [{"heading": 절 제목, "items": [("text", 글) | ("table", 행렬), ...]}] 를 원본 서식에 넣어 out 에 저장.
    돌려주는 것: {"filled": [제목…], "skipped": [제목…], "folded": [소제목…], "paragraphs": n, "tables": n, "tables_updated": n,
    "existing_kept": n, "boxes_removed": n, "linesegs_removed": n, "sections_changed": [항목 이름…]}"""
    src, out = Path(src), Path(out)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        header = z.read("Contents/header.xml").decode("utf-8")
        raw = {n: z.read(n).decode("utf-8") for n in names if _SEC_RE.search(n)}
    pp, cp = _default_refs(header)
    solid = _solid_border_ids(header)
    next_id = _max_numeric_id([header, *raw.values()]) + 1
    secs = [_Section(n, raw[n], solid) for n in sorted(raw, key=lambda n: int(_SEC_RE.search(n).group(1)))]
    known = _Known()
    for s in secs:
        known.add_xml(s.xml)
    seen_texts: set[str] = set()
    seen_tables: set[frozenset] = set()
    all_tables = [t for s in secs for t in s.tables]
    report = {"filled": [], "skipped": [], "folded": [], "duplicates": [], "paragraphs": 0, "tables": 0, "tables_updated": 0,
              "existing_kept": 0, "boxes_removed": 0, "linesegs_removed": 0, "sections_changed": []}

    # 1) 절 제목 자리 찾기 — 못 찾은 소제목(점 번호 없음)은 바로 앞 절에 잇는다
    located: list[tuple[_Section, int, dict, list]] = []          # (구역, 삽입 위치, body, items)
    for body in bodies:
        hit = None
        for s in secs:
            idx = next((i for i, t in enumerate(s.texts) if t and _heading_matches(t, body["heading"])), None)
            if idx is not None:
                hit = (s, idx)
                break
        if hit is None:
            if located and not _DOTTED.match(body["heading"] or ""):
                located[-1][3].append(("text", body["heading"]))
                located[-1][3].extend(body.get("items", []))
                report["folded"].append(body["heading"])
            else:
                report["skipped"].append(body["heading"])
            continue
        s, idx = hit
        at = s.paras[idx][1]
        if idx + 1 < len(s.paras):
            nxt = s.xml[s.paras[idx + 1][0]:s.paras[idx + 1][1]]
            if "<hp:tbl" in nxt and _BOX_RE.search(nxt):
                at = s.paras[idx + 1][1]
        located.append((s, at, body, list(body.get("items", []))))

    injected_bf: str | None = None
    replaced: dict[str, bytes] = {}

    # 2) 절마다 작업본 내용을 서식과 견줘 넣는다 — 커서는 서식 안 위치, 이미 있는 것은 지나가고 새것은 커서에 쌓는다
    for s, cursor, body, items in located:
        pending: list[str] = []
        put_any = False
        widths: list[float] | None = None                  # 바로 다음 표의 열 너비(작업본에서 온 것)

        def flush() -> None:
            nonlocal pending
            if pending:
                s.splices.append((cursor, cursor, "".join(pending)))
                pending = []

        for kind, payload in items:
            if kind == "widths":
                widths = list(payload) if payload else None
                continue
            if kind == "text":
                for line in str(payload).split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    n = _norm(line)
                    if len(n) > 1 and known.has(n):
                        # 서식에 있는 문단 — 이 구역의 커서 뒤에 있으면 그 뒤로 옮겨 간다
                        j = next((i for i, (a, b) in enumerate(s.paras) if a >= cursor and _norm(s.texts[i]) == n), None)
                        report["existing_kept"] += 1
                        if j is not None:
                            flush()
                            cursor = s.paras[j][1]
                        continue
                    if len(n) >= 30:
                        if n in seen_texts:                       # 작업본 안에서 같은 새 문단이 또 나옴 — 앞의 것만 넣고 보고
                            report["duplicates"].append(f"{body['heading'][:20]}: {line[:40]}")
                            continue
                        seen_texts.add(n)
                    pending.append(paragraph_xml(line, pp, cp))
                    report["paragraphs"] += 1
                    put_any = True
            elif kind == "table" and payload:
                rows = [[str(c) for c in r] for r in payload]
                dcells = _docs_cells(rows)
                if not dcells:
                    continue
                widths_here, widths = widths, None                  # 이 표에 딸린 열 너비(새 표로 넣을 때만 쓴다)
                if all(known.has(c) for c in dcells) or any(dcells <= t.cells for t in all_tables):
                    # 서식에 그대로 있는 표 — 이 구역 커서 뒤의 것이면 그 뒤로
                    here = next((t for t in s.tables if t.para[0] >= cursor and dcells <= t.cells), None)
                    report["existing_kept"] += 1
                    if here is not None:
                        flush()
                        cursor = here.para[1]
                        here.consumed = True
                    continue
                key = frozenset(dcells)
                if len(dcells) >= 3:
                    if key in seen_tables:                        # 작업본 안에서 같은 새 표가 또 나옴
                        report["duplicates"].append(f"{body['heading'][:20]}: 표 {rows[0][0][:20] if rows[0] else ''}")
                        continue
                    seen_tables.add(key)
                # 서식 표를 고쳐 쓴 것인가 — 이 구역에서 칸이 가장 많이 겹치는 표(첫 행이 같으면 우선)에 칸 대응이 되면 그 칸에 써 넣는다
                cand = None
                best = 0.0
                for t in s.tables:
                    if t.consumed or _BOX_RE.search(s.xml[t.para[0]:t.para[1]]):
                        continue
                    first_same = bool(t.rows and rows) and [_norm(c.text) for c in t.rows[0] if _norm(c.text)] == [_norm(c) for c in rows[0] if _norm(c)]
                    score = len(dcells & t.cells) / len(dcells) + (1.0 if first_same else 0.0) + (0.1 if t.para[0] >= cursor else 0.0)
                    if score > best:
                        best, cand = score, t
                pairs = _map_cells(cand, rows) if cand is not None and best >= 0.4 else None
                if pairs:
                    flush()
                    for fc, text in pairs:
                        if fc.nested or _norm(fc.text) == _norm(text):
                            continue
                        p_open = fc.p_open or f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                        paras_xml = "".join(f'{p_open}<hp:run charPrIDRef="{fc.char_ref}"><hp:t>{escape(ln.strip())}</hp:t></hp:run></hp:p>'
                                            for ln in (text.split("\n") or [""]))
                        s.splices.append((fc.inner[0], fc.inner[1], paras_xml))
                    cand.consumed = True
                    report["tables_updated"] += 1
                    put_any = True
                    if cand.para[0] >= cursor:
                        cursor = cand.para[1]
                    continue
                bf = s.bf
                if bf is None:
                    if injected_bf is None:
                        header, injected_bf = _inject_border_fill(header)
                        replaced["Contents/header.xml"] = header.encode("utf-8")
                    bf = s.bf = injected_bf
                t = table_xml(rows, pp, cp, s.width, bf, s.margin, next_id, widths_here)
                if t:
                    pending.append(t)
                    report["tables"] += 1
                    next_id += 1
                    put_any = True
        flush()
        if put_any:
            report["filled"].append(body["heading"])

    # 3) 구역마다 splice 를 뒤에서부터 적용(앞 위치가 밀리지 않게), 바뀐 구역은 줄 배치 캐시를 지운다
    for s in secs:
        xml = s.xml
        changed = bool(s.splices)
        for a, b, chunk in sorted(s.splices, key=lambda x: -x[0]):
            xml = xml[:a] + chunk + xml[b:]
        if remove_boxes:
            xml, n = _remove_boxes(xml)
            report["boxes_removed"] += n
            changed = changed or n > 0
        if changed:
            xml, n = strip_linesegs(xml)
            report["linesegs_removed"] += n
            replaced[s.name] = xml.encode("utf-8")
            report["sections_changed"].append(s.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_patched(src, out, replaced)
    return report
