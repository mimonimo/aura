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

import difflib
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
_FORM_HEAD = re.compile(r"\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*\.|\d+(?:\.\d+)*\.?\s+\S|【)")    # 서식의 다음 절 제목(번호·【】)
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
    width: int = 0                    # cellSz width(HWPUNIT) — 작업본 격자와 x 위치로 맞출 때


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
            wm = re.search(r"<hp:cellSz\b[^>]*width=\"(\d+)\"", xml[ca:cb])
            row.append(_Cell(tc=(ca, cb), inner=(ia, ib), col=int(addr.group(1)) if addr else len(row),
                             text=" ".join(" ".join(_texts(_without_tables(inner))).split()),
                             nested="<hp:tbl" in inner, p_open=pm.group(0) if pm else "", char_ref=cm.group(1) if cm else "0",
                             full=_norm(" ".join(_texts(inner))), width=int(wm.group(1)) if wm else 0))
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


def _filled_border_ids(header_xml: str) -> set[str]:
    """채움색(음영)이 있는 borderFill id — 머리 행·라벨 열 음영의 후보."""
    out: set[str] = set()
    for m in re.finditer(r"<hh:borderFill\b[^>]*\bid=\"(\d+)\"[^>]*>(.*?)</hh:borderFill>", header_xml, re.S):
        face = re.search(r"<hc:winBrush\b[^>]*faceColor=\"(#[0-9A-Fa-f]{6})\"", m.group(2))
        if face and face.group(1).upper() not in ("#FFFFFF",):
            out.add(m.group(1))
    return out


_ROLE_NUM = re.compile(r"^[\s\d,.\-+%()~/△▲▼±]*\d[\s\d,.\-+%()~/△▲▼±원천만억개명건회년월일점배]*$")


def _role_styles(sections: list["_Section"], header_xml: str) -> dict[str, tuple[str, str, str]]:
    """서식 본문 표들의 역할별 최빈 모양 {head|label|body: (borderFillIDRef, paraPrIDRef, charPrIDRef)} — 새 표가 서식 표처럼
    보이게(kordoc 표 서식 프로필의 원리를 역할 단위로: 표 통째 복제는 새 표와 앵커가 맞지 않는다, 2026-09-30 조사).
    머리·라벨은 음영이 있을 때만 낸다(음영 없는 서식이면 지금처럼 한 모양)."""
    filled = _filled_border_ids(header_xml)
    count: dict[str, Counter] = {"head": Counter(), "label": Counter(), "body": Counter()}
    for s in sections:
        for t in s.tables:
            if len(t.rows) < 2 or _BOX_RE.search(s.xml[t.para[0]:t.para[1]]):
                continue
            for r, row in enumerate(t.rows):
                for c in row:
                    if c.nested:
                        continue
                    tc_open = s.xml[c.tc[0]:s.xml.index(">", c.tc[0]) + 1]
                    bf = re.search(r"borderFillIDRef=\"(\d+)\"", tc_open)
                    pp = re.search(r"paraPrIDRef=\"(\d+)\"", c.p_open)
                    if not bf or not pp:
                        continue
                    role = "head" if r == 0 else "label" if c.col == 0 else "body"
                    count[role][(bf.group(1), pp.group(1), c.char_ref)] += 1
    out: dict[str, tuple[str, str, str]] = {}
    for role, cnt in count.items():
        if not cnt:
            continue
        best = cnt.most_common(1)[0][0]
        if role == "body" or best[0] in filled:
            out[role] = best
    return out


def _label_like(rows: list[list[str]]) -> bool:
    """새 표의 첫 열이 라벨 열인가 — 첫 열이 짧은 글이고 나머지 칸의 반 이상이 수치나 긴 글(md_docx 와 같은 판정)."""
    body = rows[1:]
    first = [r[0] for r in body if r and str(r[0]).strip()]
    rest = [str(x) for r in body for x in r[1:] if str(x).strip()]
    return (len(body) >= 2 and bool(first) and all(len(x) <= 16 and not _ROLE_NUM.match(x) for x in first) and bool(rest)
            and sum(1 for x in rest if _ROLE_NUM.match(x) or len(x) > 16) / len(rest) >= 0.5)


_MARK = re.compile(r"^\s*([□■○◦ㅇ●◎◇◆▪▫•ㆍ·※\-–]|\d{1,2}\)|[가-하]\)|\d{1,2}\.(?!\d)|[가-하]\.|[①-⑳])")


def marker_of(text: str) -> str:
    """문단 첫 부호(개조식) — 번호는 꼴만 본다(1) 과 3) 은 같은 꼴 'n)')."""
    m = _MARK.match(text or "")
    if not m:
        return ""
    k = m.group(1)
    if re.fullmatch(r"\d{1,2}\)", k):
        return "n)"
    if re.fullmatch(r"[가-하]\)", k):
        return "가)"
    if re.fullmatch(r"\d{1,2}\.", k):
        return "n."
    if re.fullmatch(r"[가-하]\.", k):
        return "가."
    if re.fullmatch(r"[①-⑳]", k):
        return "①"
    return {"·": "ㆍ", "–": "-", "▫": "▪"}.get(k, k)


class _ParaStyles:
    """서식 본문 문단(표·상자 밖 최상위)의 모양 — 부호별 최빈 (문단 모양, 글자 모양), 짧은 굵은 줄(소제목)과 긴 문장(본문)을 나눠 센다.
    새 문단이 서식 작성자가 정한 위계를 따르게(kordoc 조사 5순위, 2026-09-30). 서식에서 뽑는 일반 규칙이다."""

    SHORT = 30

    def __init__(self, sections: list["_Section"], header_xml: str) -> None:
        self.bold = {m.group(1) for m in re.finditer(r"<hh:charPr\b[^>]*\bid=\"(\d+)\"[^>]*>(?:(?!</hh:charPr>).)*<hh:bold\b", header_xml, re.S)}
        heights = {m.group(1): int(m.group(2)) for m in re.finditer(r'<hh:charPr\b[^>]*\bid="(\d+)"[^>]*\bheight="(\d+)"', header_xml)}
        body_h = heights.get(_default_refs(header_xml)[1], 1000)
        self.by: dict[tuple[str, bool], Counter] = {}
        for s in sections:
            for (a, b), t in zip(s.paras, s.texts):
                block = s.xml[a:b]
                if not t or "<hp:tbl" in block or "<hp:pic" in block or "<hp:secPr" in block:
                    continue
                mk = marker_of(t)
                if not mk:
                    continue
                pp = re.search(r"paraPrIDRef=\"(\d+)\"", block[:block.index(">") + 1])
                cp = re.search(r"<hp:run\b[^>]*charPrIDRef=\"(\d+)\"", block)
                if not pp or not cp:
                    continue
                head_like = len(t) <= self.SHORT and cp.group(1) in self.bold
                if not head_like and heights.get(cp.group(1), body_h) > body_h + 100:
                    continue                                    # 본문보다 큰 글자는 번호 붙은 제목 — 긴 문장의 본으로 쓰지 않는다
                self.by.setdefault((mk, head_like), Counter())[(pp.group(1), cp.group(1))] += 1

    def pick(self, line: str) -> tuple[str, str] | None:
        mk = marker_of(line)
        if not mk:
            return None
        head_like = len(line) <= self.SHORT
        got = self.by.get((mk, head_like))
        return got.most_common(1)[0][0] if got else None


def _marker_em(mk: str) -> float:
    """부호 폭(글자 크기 배수) 어림 — 전각 부호 1, 숫자·반각 문자는 좁게. 한글이 조판 때 탭 폭을 다시 계산하므로 어림이면 된다."""
    if not mk:
        return 0.0
    return sum(0.55 if ch.isascii() and ch not in "-" else 0.4 if ch == "-" else 1.0 for ch in mk)


class _HangRegistry:
    """개조식 내어쓰기 문단 모양 등록기(kordoc style-registry 의 원리) — 바탕 모양을 복제해 내어쓰기(intent 음수)와 자동 탭을 붙인
    새 paraPr 를 header 에 한 번만 등록한다(같은 사양은 같은 id). 한컴 paraPr 여백은 hp:case(HwpUnitChar, 값이 절반)와 hp:default
    두 벌이라 둘 다 바꾼다. 바탕 모양이 이미 내어쓰기·들여쓰기를 가지면 서식 작성자의 뜻이라 건드리지 않는다."""

    def __init__(self, header_xml: str) -> None:
        self.header = header_xml
        self.cache: dict[tuple[str, int], str] = {}
        m = re.search(r'<hh:tabPr\b[^>]*\bid="(\d+)"[^>]*\bautoTabLeft="1"', header_xml)
        self.auto_tab = m.group(1) if m else ""
        self.changed = False

    def char_height(self, cp: str) -> int:
        m = re.search(rf'<hh:charPr\b[^>]*\bid="{cp}"[^>]*\bheight="(\d+)"', self.header)
        return int(m.group(1)) if m else 1000

    def hanging(self, pp: str, cp: str, mk: str) -> tuple[str, int]:
        """(쓸 paraPr id, 내어쓰기 폭 HWPUNIT). 등록할 수 없으면 (pp, 0)."""
        if not self.auto_tab or not mk:
            return pp, 0
        hang = int(self.char_height(cp) * (_marker_em(mk) + 0.5))
        key = (pp, hang)
        if key in self.cache:
            return self.cache[key], hang
        m = re.search(rf'<hh:paraPr\b[^>]*\bid="{pp}"[^>]*>.*?</hh:paraPr>', self.header, re.S)
        if not m or re.search(r'<hc:intent value="-?[1-9]', m.group(0)):
            return pp, 0
        ids = [int(x) for x in re.findall(r'<hh:paraPr\b[^>]*\bid="(\d+)"', self.header)]
        new_id = str(max(ids) + 1)
        body = m.group(0)
        body = re.sub(r'(<hh:paraPr\b[^>]*\bid=")\d+(")', rf"\g<1>{new_id}\2", body, count=1)
        body = re.sub(r'(<hh:paraPr\b[^>]*\btabPrIDRef=")\d+(")', rf"\g<1>{self.auto_tab}\2", body, count=1)
        case = re.search(r"<hp:case\b.*?</hp:case>", body, re.S)
        if case:
            body = body[:case.start()] + case.group(0).replace('<hc:intent value="0"', f'<hc:intent value="{-(hang // 2)}"') + body[case.end():]
        dflt = re.search(r"<hp:default>.*?</hp:default>", body, re.S)
        if dflt:
            body = body[:dflt.start()] + dflt.group(0).replace('<hc:intent value="0"', f'<hc:intent value="{-hang}"') + body[dflt.end():]
        elif not case:
            body = body.replace('<hc:intent value="0"', f'<hc:intent value="{-hang}"', 1)
        close = self.header.find("</hh:paraProperties>")
        if close < 0:
            return pp, 0
        head = self.header[:close] + body + self.header[close:]
        cnt = re.search(r'(<hh:paraProperties\b[^>]*\bitemCnt=")(\d+)(")', head)
        if cnt:
            head = head[:cnt.start(2)] + str(int(cnt.group(2)) + 1) + head[cnt.end(2):]
        self.header = head
        self.cache[key] = new_id
        self.changed = True
        return new_id, hang


def marker_paragraph_xml(text: str, pp: str, cp: str, tab_hu: int) -> str:
    """부호 문단 — 부호 뒤 공백 대신 탭(자동 탭이 내어쓰기 자리로 보낸다). 부호가 없거나 탭을 못 쓰면 보통 문단."""
    m = _MARK.match(text or "")
    if not m or not tab_hu:
        return paragraph_xml(text, pp, cp)
    head, rest = text[:m.end()], text[m.end():].lstrip()
    return (f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{cp}"><hp:t>{xml_text(head.strip())}<hp:tab width="{tab_hu}" leader="0" type="1"/>{xml_text(rest)}</hp:t></hp:run></hp:p>')


def _slot_style(s: "_Section", cursor: int) -> tuple[str, str] | None:
    """서식이 절 제목(또는 안내 상자) 바로 뒤에 남겨 둔 빈 쓰기 자리의 모양 — 부호 없는 본문 문장에 쓴다."""
    for (a, b), t in zip(s.paras, s.texts):
        if a < cursor:
            continue
        block = s.xml[a:b]
        if t or "<hp:tbl" in block or "<hp:pic" in block:
            return None                                        # 바로 뒤가 글·표면 쓰기 자리가 없는 서식
        pp = re.search(r"paraPrIDRef=\"(\d+)\"", block[:block.index(">") + 1])
        cp = re.search(r"charPrIDRef=\"(\d+)\"", block)
        return (pp.group(1), cp.group(1)) if pp and cp else None
    return None


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def xml_text(text: str) -> str:
    """새 글을 XML 글자로 — XML 1.0 이 금하는 제어문자를 지운다(한 글자만 섞여도 구역이 깨져 한글이 문서를 못 연다, kordoc
    escapeXmlText 의 원리). 줄 나눔은 부르는 쪽이 문단으로 나눈 뒤라 여기 오는 \x0b 등은 버린다."""
    return escape(_CTRL.sub("", text))


def split_lines(text: str) -> list[str]:
    """문단 나누기 — 독스의 문단 안 줄 바꿈(Shift+Enter 는 API 에서 \x0b)과 \r 도 줄로 본다."""
    return re.split(r"\r\n|[\n\r\x0b\x0c\u2028\u2029]", text)


def paragraph_xml(text: str, pp: str, cp: str) -> str:
    return (f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{cp}"><hp:t>{xml_text(text)}</hp:t></hp:run></hp:p>')


def table_xml(rows: list[list[str]], pp: str, cp: str, width_hu: int, bf: str, margin: str, table_id: int,
              widths: list[float] | None = None, roles: dict[str, tuple[str, str, str]] | None = None) -> str:
    """새 표 XML. widths(작업본 표의 열 너비, 단위 무관)가 있으면 그 비율로, 없으면 본문 폭을 균등 분할.
    roles(서식 표의 역할별 모양, _role_styles)가 있으면 머리 행·라벨 열·본문 칸에 그 테두리·문단·글자 모양을 쓴다."""
    roles = roles or {}
    label = "label" in roles and _label_like(rows)
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
            lines = split_lines(str(cell)) or [""]
            role = "head" if r == 0 and "head" in roles else "label" if c == 0 and label else "body" if "body" in roles else ""
            cbf, cpp, ccp = roles[role] if role else (bf, pp, cp)
            paras = "".join(paragraph_xml(ln, cpp, ccp) for ln in lines)
            tcs.append(f'<hp:tc name="" header="{1 if r == 0 else 0}" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{cbf}">'
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


_IMG_KINDS = ((b"\x89PNG", "png", "image/png"), (b"\xff\xd8", "jpg", "image/jpg"), (b"GIF8", "gif", "image/gif"), (b"BM", "bmp", "image/bmp"))
_MANIFEST_ID = re.compile(r'<opf:item\s[^>]*?\bid="([^"]+)"')


def image_kind(data: bytes) -> tuple[str, str] | None:
    """그림 바이트의 (확장자, media-type) — 한글이 읽는 네 가지만."""
    return next(((ext, mt) for magic, ext, mt in _IMG_KINDS if data.startswith(magic)), None)


def _image_px(data: bytes) -> tuple[int, int] | None:
    """PNG·JPEG 픽셀 크기(가로·세로 비율용). 모르면 None."""
    if data.startswith(b"\x89PNG") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker, seg = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + seg
    return None


def picture_xml(bin_id: str, pic_id: int, width_hu: int, height_hu: int, org_w: int, org_h: int, pp: str, cp: str) -> str:
    """글자처럼 취급하는 그림 한 개를 담은 문단 — 실물 hwpx 의 hp:pic 모양(treatAsChar·위아래 배치) 그대로, 크기만 우리 값."""
    return (f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{cp}">'
            f'<hp:pic id="{pic_id}" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
            f'href="" groupLevel="0" instid="{pic_id}" reverse="0"><hp:offset x="0" y="0"/><hp:orgSz width="{org_w}" height="{org_h}"/>'
            f'<hp:curSz width="{width_hu}" height="{height_hu}"/><hp:flip horizontal="0" vertical="0"/>'
            f'<hp:rotationInfo angle="0" centerX="{width_hu // 2}" centerY="{height_hu // 2}" rotateimage="1"/>'
            f'<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            f'<hc:scaMatrix e1="{width_hu / org_w:.6f}" e2="0" e3="0" e4="0" e5="{height_hu / org_h:.6f}" e6="0"/>'
            f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
            f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{org_w}" y="0"/><hc:pt2 x="{org_w}" y="{org_h}"/><hc:pt3 x="0" y="{org_h}"/></hp:imgRect>'
            f'<hp:imgClip left="0" right="{org_w}" top="0" bottom="{org_h}"/><hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:imgDim dimwidth="{org_w}" dimheight="{org_h}"/>'
            f'<hc:img binaryItemIDRef="{bin_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/><hp:effects/>'
            f'<hp:sz width="{width_hu}" widthRelTo="ABSOLUTE" height="{height_hu}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="1" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
            f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/><hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'</hp:pic></hp:run><hp:run charPrIDRef="{cp}"><hp:t/></hp:run></hp:p>')


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


def _write_patched(src: Path, out: Path, replaced: dict[str, bytes], added: dict[str, bytes] | None = None) -> None:
    """원본 ZIP 의 항목 순서·압축 방식을 지키고 바뀐 항목만 새 내용으로 쓴다(kordoc zip-patch 의 원리). 새 항목(그림)은 끝에 무압축으로."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            data = replaced.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            ni.compress_type = info.compress_type if info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) else zipfile.ZIP_DEFLATED
            ni.external_attr = info.external_attr
            zout.writestr(ni, data)
        for name, data in (added or {}).items():
            zout.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data, compress_type=zipfile.ZIP_STORED)


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


def _grid_starts(form: _FormTable) -> dict[int, tuple[float, float]]:
    """표 격자 열의 x 시작(비율)과 너비(비율) — colAddr 별. 표 폭을 다 덮는 행(칸 너비 합이 가장 큰 행)의 칸으로 격자를 세우고,
    그 행에 없는 colAddr 는 다른 행의 칸 위치로 채운다. 위 행 rowSpan 에 가려 칸이 몇 개뿐인 행도 이 격자로 자리를 안다."""
    if not form.rows:
        return {}
    ref = max(form.rows, key=lambda r: sum(c.width for c in r))
    total = float(sum(c.width for c in ref)) or 1.0
    starts: dict[int, tuple[float, float]] = {}
    x = 0.0
    for c in ref:
        starts[c.col] = (x / total, c.width / total)
        x += c.width
    for row in form.rows:                                          # 참조 행에 없는 열(병합으로 갈라진 자리)은 앞 칸의 끝으로
        x = 0.0
        for c in row:
            if c.col not in starts:
                starts[c.col] = (x / total, c.width / total)
            x = (starts[c.col][0] * total) + c.width
    return starts


def _map_cells(form: _FormTable, rows: list[list[str]], widths: list[float] | None = None) -> list[tuple[_Cell, str]] | None:
    """작업본 표의 칸 → 서식 표의 칸. 행 수가 같아야 한다. 행마다 칸 수가 같으면 순서대로, 작업본 행이 격자 열 수와 같으면 colAddr 로,
    작업본에 열 너비가 오면(독스는 병합 표를 잘게 나눈 격자로 낸다 — 실측 2026-09-29: 서식 6열이 독스 14열) x 위치로 맞춘다."""
    if len(rows) != len(form.rows):
        return None
    if widths and all(len(r) == len(widths) for r in rows) and sum(widths) > 0 and all(c.width for r in form.rows for c in r):
        total_d = float(sum(widths))
        grid = _grid_starts(form)                                    # colAddr → 표 왼쪽부터의 x(비율). 위 행의 rowSpan 에 가려 짧은 행도 맞는다
        if not grid:
            return None
        total_f = float(max(sum(c.width for c in r) for r in form.rows)) or 1.0
        pairs: list[tuple[_Cell, str]] = []
        for drow, frow in zip(rows, form.rows):
            got: dict[int, list[str]] = {}
            xd = 0.0
            for ci, dc in enumerate(drow):
                w = float(widths[ci])
                if str(dc).strip():
                    # 작업본 칸의 가운데가 들어가는 서식 칸 — 독스가 병합 칸을 잘게 나눠 격자가 서식과 어긋나도 맞는다
                    # (리허설 2026-10-08: 서식 11열·독스 21열 요약서 표가 시작 위치 비교로 대응 실패 → 새 표로 덧붙음)
                    mid = (xd + w / 2) / total_d
                    hit = next((i for i, fc in enumerate(frow)
                                if fc.col in grid and grid[fc.col][0] - 0.005 <= mid < grid[fc.col][0] + fc.width / total_f + 0.005), None)
                    if hit is None:
                        pos = xd / total_d
                        diff, hit = min((abs(grid.get(fc.col, (9.0, 0.0))[0] - pos), i) for i, fc in enumerate(frow))
                        if diff > max(0.3 * grid.get(frow[hit].col, (0.0, 0.05))[1], 0.01):
                            return None                              # 자리가 안 맞는 값 칸이 있다 — 대응 불가
                    got.setdefault(hit, []).append(str(dc).strip())
                xd += w
            pairs += [(frow[i], " ".join(v)) for i, v in sorted(got.items())]
        return pairs
    pairs = []
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


def _cell_with_text(tc_xml: str, text: str) -> str:
    """칸 XML 의 글을 바꾼다 — 첫 문단의 여는 태그·글자 모양을 이어 쓰고, 줄마다 문단 하나. 줄 배치 캐시는 버린다."""
    sa = tc_xml.find("<hp:subList")
    if sa < 0:
        return tc_xml
    ia = tc_xml.index(">", sa) + 1
    ib = tc_xml.rfind("</hp:subList>")
    inner = tc_xml[ia:ib]
    pm = re.search(r"<hp:p\b[^>]*>", inner)
    cm = re.search(r"charPrIDRef=\"(\d+)\"", inner)
    p_open = pm.group(0) if pm else '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
    cp = cm.group(1) if cm else "0"
    paras = "".join(f'{p_open}<hp:run charPrIDRef="{cp}"><hp:t>{xml_text(ln.strip())}</hp:t></hp:run></hp:p>'
                    for ln in (split_lines(text) or [""]))
    return tc_xml[:ia] + paras + tc_xml[ib:]


def grow_table(xml: str, form: _FormTable, rows: list[list[str]]) -> str | None:
    """서식 표에 작업본 행을 채우고 모자라면 빈 입력 행을 복제해 늘린 표 문단 XML(원래 표 문단을 통째로 바꿀 것). 못 하면 None.

    서식에서 글이 있는 행(머리 행·소계·합계 행)을 닻으로 작업본 행과 차례대로 맞추고, 닻 사이의 빈 입력 행에 작업본 데이터 행을
    넣는다. 모자라면 그 구간의 마지막 빈 행을 복제한다(kordoc table-rows 의 원리: 행 복제·rowAddr 다시 매김·rowCnt·표 높이).
    복제할 행이 세로 병합 묶음 안(첫 열 '영역' 칸 아래 등)이면 그 묶음 머리 칸의 rowSpan·높이를 복제한 만큼 늘린다(kordoc 이 막아 둔
    경우를 더한 것). 복제할 행 자체가 병합을 시작하거나 중첩 표·칸 수가 안 맞으면 하지 않는다 — 안전하게 새 표로 넣고 보고한다.
    칸 수가 다른 닻 행(머리 병합)은 글을 건드리지 않는다."""
    a, b = form.para
    para = xml[a:b]
    tbls = _top_level(para, "hp:tbl")
    if not tbls:
        return None
    ta, tb = tbls[0]
    tbl = para[ta:tb]
    if tbl.count("<hp:tbl") > 1:
        return None
    trs = _top_level(tbl, "hp:tr")
    frows = []
    cc = re.search(r'colCnt="(\d+)"', tbl[:tbl.index(">") + 1])
    col_cnt = int(cc.group(1)) if cc else 0
    for i, (ra, rb) in enumerate(trs):
        tr = tbl[ra:rb]
        tcs = [tr[x:y] for x, y in _top_level(tr, "hp:tc")]
        texts = [_norm(" ".join(_texts(tc))) for tc in tcs]
        spans = [int(m) for tc in tcs for m in re.findall(r'<hp:cellSpan\b[^>]*rowSpan="(\d+)"', tc)]
        cols = [int(m.group(1)) if (m := re.search(r'<hp:cellAddr\b[^>]*colAddr="(\d+)"', tc)) else k for k, tc in enumerate(tcs)]
        frows.append({"xml": tr, "tcs": tcs, "texts": texts, "blank": not any(texts), "i": i, "spans": max(spans + [1]), "cols": cols})

    def values(fr: dict, d: list[str]) -> list[str] | None:
        """작업본 행의 값을 서식 행의 칸 차례로 — 칸 수가 같으면 차례대로, 작업본 행이 격자 열 수이면 colAddr 로(병합에 가려진 열의
        값은 비어 있거나 묶음 머리 글과 같아야 한다). 못 맞추면 None."""
        if len(d) == len(fr["tcs"]):
            return [str(x) for x in d]
        if col_cnt and len(d) == col_cnt:
            hidden = [str(d[c]) for c in range(col_cnt) if c not in fr["cols"]]
            if any(_norm(x) for x in hidden):
                return None
            return [str(d[c]) for c in fr["cols"]]
        return None
    if len(rows) <= len(frows) or not frows:
        return None
    norm_rows = [[_norm(str(c)) for c in r] for r in rows]
    # 닻 맞추기 — 서식의 글 있는 행은 그 칸 글이 모두 작업본 행에 들어 있어야 한다(차례대로)
    anchors: list[tuple[int, int]] = []
    j = 0
    for fi, fr in enumerate(frows):
        if fr["blank"]:
            continue
        want = {t for t in fr["texts"] if t}
        while j < len(rows) and not want <= set(norm_rows[j]):
            j += 1
        if j >= len(rows):
            return None
        anchors.append((fi, j))
        j += 1
    bounds = [(-1, -1)] + anchors + [(len(frows), len(rows))]
    plan: list[tuple[dict, list[str] | None]] = []        # (서식 행, 넣을 작업본 행)
    for (f0, w0), (f1, w1) in zip(bounds, bounds[1:]):
        if f0 >= 0:
            plan.append((frows[f0], rows[w0]))
        blanks = frows[f0 + 1:f1]
        data = rows[w0 + 1:w1]
        if len(data) > len(blanks) and not blanks:
            return None                                       # 복제할 빈 행이 없는 구간
        for k, d in enumerate(data):
            tpl = blanks[k] if k < len(blanks) else blanks[-1]
            if values(tpl, d) is None:
                return None
            if k >= len(blanks) and tpl["spans"] > 1:
                return None                                   # 복제할 행이 세로 병합을 시작한다 — 복제하면 격자가 깨진다
            plan.append((tpl, d))
        plan.extend((bl, None) for bl in blanks[len(data):])
    # 복제 수 — 행마다. 복제할 행을 덮는 세로 병합(묶음 머리 칸)은 그만큼 rowSpan·높이를 늘린다(묶음 안에 행 넣기)
    clones: dict[int, int] = {}
    seen_ids: set[int] = set()
    for fr, _d in plan:
        if id(fr) in seen_ids:
            clones[fr["i"]] = clones.get(fr["i"], 0) + 1
        seen_ids.add(id(fr))
    tpl_h = {i: max((int(h) for h in re.findall(r'<hp:cellSz\b[^>]*height="(\d+)"', frows[i]["xml"])), default=0) for i in clones}
    grow_span: dict[tuple[int, int], tuple[int, int]] = {}     # (행, 칸) → (더할 행 수, 더할 높이)
    for q, fr in enumerate(frows):
        for c, tc in enumerate(fr["tcs"]):
            m = re.search(r'<hp:cellSpan\b[^>]*rowSpan="(\d+)"', tc)
            rs = int(m.group(1)) if m else 1
            if rs > 1:
                inside = [t for t in clones if q < t <= q + rs - 1]
                if inside:
                    grow_span[(q, c)] = (sum(clones[t] for t in inside), sum(clones[t] * tpl_h[t] for t in inside))
    # 새 행들 — 칸 글을 바꾸고 rowAddr 를 차례로
    new_trs, grown = [], 0
    seen: set[int] = set()
    for r, (fr, d) in enumerate(plan):
        clone = id(fr) in seen
        seen.add(id(fr))
        grown += clone
        tcs = []
        vals = values(fr, d) if d is not None else None
        for c, tc in enumerate(fr["tcs"]):
            if not clone and (fr["i"], c) in grow_span:
                add_r, add_h = grow_span[(fr["i"], c)]
                tc = re.sub(r'(<hp:cellSpan\b[^>]*rowSpan=")(\d+)(")', lambda m: f"{m.group(1)}{int(m.group(2)) + add_r}{m.group(3)}", tc, count=1)
                tc = re.sub(r'(<hp:cellSz\b[^>]*height=")(\d+)(")', lambda m: f"{m.group(1)}{int(m.group(2)) + add_h}{m.group(3)}", tc, count=1)
            if vals is not None and (clone or _norm(vals[c]) != fr["texts"][c]):
                tc = _cell_with_text(tc, vals[c])
            elif clone:
                tc = _cell_with_text(tc, "")
            tcs.append(re.sub(r'(<hp:cellAddr\b[^>]*\browAddr=")\d+(")', rf"\g<1>{r}\2", tc))
        tr = fr["xml"]
        head_end = tr.index(">") + 1
        new_trs.append(tr[:head_end] + "".join(tcs) + "</hp:tr>")
    body = tbl[:trs[0][0]] + "".join(new_trs) + tbl[trs[-1][1]:]
    open_end = body.index(">") + 1
    head = re.sub(r'\browCnt="\d+"', f'rowCnt="{len(plan)}"', body[:open_end])
    body = head + body[open_end:]
    if grown:
        add_h = sum(n * tpl_h[t] for t, n in clones.items())
        body = re.sub(r'(<hp:sz\b[^>]*height=")(\d+)(")', lambda m: f"{m.group(1)}{int(m.group(2)) + add_h}{m.group(3)}", body, count=1)
    body, _n = strip_linesegs(body)
    return para[:ta] + body + para[tb:]


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


_HEAD_LISTS = (("paraProperties", "paraPr", "paraPrIDRef"), ("charProperties", "charPr", "charPrIDRef"),
               ("borderFills", "borderFill", "borderFillIDRef"), ("styles", "style", "styleIDRef"))


def check(path: Path | str) -> list[str]:
    """채운 한글 파일의 자가 점검 — 문제 목록(비면 통과). kordoc validate 가 보는 것(ZIP 규약·웰폼드·secCnt·manifest)에 더해
    그것이 안 보는 참조 무결성(본문이 가리키는 문단·글자·테두리·스타일 id 가 header 에 있는지, 목록 itemCnt, 그림 참조)과
    표 격자(rowCnt = 행 수, 칸 덮개 합 = rowCnt × colCnt)를 본다. 모양을 새로 등록하거나 행을 늘릴 때의 회귀 방지선."""
    import xml.etree.ElementTree as ET

    probs: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return ["ZIP 이 아니다"]
    infos = z.infolist()
    if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED \
            or z.read("mimetype").strip() != b"application/hwp+zip":
        probs.append("mimetype 이 무압축 첫 항목(application/hwp+zip)이 아니다")
    names = set(z.namelist())
    trees: dict[str, ET.Element] = {}
    for n in sorted(names):
        if n.endswith((".xml", ".hpf", ".rdf")):
            try:
                trees[n] = ET.fromstring(z.read(n))
            except ET.ParseError as e:
                probs.append(f"{n} 웰폼드 아님: {e}")
    head = trees.get("Contents/header.xml")
    secs = sorted(n for n in trees if _SEC_RE.search(n))
    if head is None:
        return probs + ["header.xml 없음"]

    def local(t: str) -> str:
        return t.rsplit("}", 1)[-1]
    if head.get("secCnt") and int(head.get("secCnt")) != len(secs):
        probs.append(f"secCnt {head.get('secCnt')} ≠ 구역 {len(secs)}")
    ids: dict[str, set[str]] = {}
    for lst, item, _ref in _HEAD_LISTS:
        box = next((e for e in head.iter() if local(e.tag) == lst), None)
        got = [e for e in (box if box is not None else []) if local(e.tag) == item]
        ids[item] = {e.get("id") for e in got}
        if box is not None and box.get("itemCnt") and int(box.get("itemCnt")) != len(got):
            probs.append(f"{lst} itemCnt {box.get('itemCnt')} ≠ 실제 {len(got)}")
    hpf = trees.get("Contents/content.hpf")
    manifest = {}
    if hpf is not None:
        for e in hpf.iter():
            if local(e.tag) == "item":
                manifest[e.get("id")] = e.get("href")
                if e.get("href") and e.get("href") not in names:
                    probs.append(f"manifest 항목 {e.get('id')} 의 파일 {e.get('href')} 없음")
    for n in secs:
        missing: dict[str, set[str]] = {}
        for e in trees[n].iter():
            for _lst, item, ref in _HEAD_LISTS:
                v = e.get(ref)
                if v is not None and v not in ids[item] and not (ref == "styleIDRef" and not ids[item]):
                    missing.setdefault(ref, set()).add(v)
            if local(e.tag) == "img" and e.get("binaryItemIDRef") and e.get("binaryItemIDRef") not in manifest:
                missing.setdefault("binaryItemIDRef", set()).add(e.get("binaryItemIDRef"))
            if local(e.tag) == "tbl":
                rows = [r for r in e if local(r.tag) == "tr"]
                rc, cc = int(e.get("rowCnt") or 0), int(e.get("colCnt") or 0)
                if rc != len(rows):
                    probs.append(f"{n} 표 {e.get('id')}: rowCnt {rc} ≠ 행 {len(rows)}")
                cover = 0
                for r in rows:
                    for tc in (c for c in r if local(c.tag) == "tc"):
                        span = next((x for x in tc if local(x.tag) == "cellSpan"), None)
                        cover += (int(span.get("colSpan") or 1) * int(span.get("rowSpan") or 1)) if span is not None else 1
                if rc and cc and cover != rc * cc:
                    probs.append(f"{n} 표 {e.get('id')}: 칸 덮개 {cover} ≠ {rc}×{cc}")
        for ref, vals in missing.items():
            probs.append(f"{n}: header 에 없는 {ref} {', '.join(sorted(vals)[:5])}")
    return probs


def fill(src: Path | str, bodies: list[dict], out: Path | str, remove_boxes: bool = False) -> dict:
    """bodies = [{"heading": 절 제목, "items": [("text", 글) | ("table", 행렬) | ("image", {data, width_pt, height_pt}), ...]}] 를 원본 서식에 넣어 out 에 저장.
    돌려주는 것: {"filled": [제목…], "skipped": [제목…], "folded": [소제목…], "paragraphs": n, "tables": n, "tables_updated": n,
    "existing_kept": n, "images": n, "images_skipped": n, "boxes_removed": n, "checks": [문제…], "linesegs_removed": n, "sections_changed": [항목 이름…]}"""
    src, out = Path(src), Path(out)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        header = z.read("Contents/header.xml").decode("utf-8")
        raw = {n: z.read(n).decode("utf-8") for n in names if _SEC_RE.search(n)}
        hpf = z.read("Contents/content.hpf").decode("utf-8") if "Contents/content.hpf" in names else None
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
    roles = _role_styles(secs, header)                  # 새 표의 머리 행·라벨 열·본문 칸 모양(서식 표에서)
    para_styles = _ParaStyles(secs, header)             # 새 문단의 부호별 모양(서식 본문 문단에서)
    hangs = _HangRegistry(header)                       # 부호 문단의 내어쓰기 모양(없으면 등록)
    report = {"filled": [], "skipped": [], "folded": [], "duplicates": [], "paragraphs": 0, "tables": 0, "tables_updated": 0,
              "existing_kept": 0, "tables_grown": 0, "images": 0, "images_skipped": 0, "boxes_removed": 0, "linesegs_removed": 0, "sections_changed": []}
    added: dict[str, bytes] = {}
    used_bin = set(_MANIFEST_ID.findall(hpf or "")) | {Path(n).stem for n in names if n.startswith("BinData/")}

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
        # 이 절의 끝 — 같은 구역(섹션 XML)에서 다음 절이 시작하는 자리. 모양으로 짝지을 서식 표는 이 앞에서만 찾는다
        nxt_head = next((a_ for (a_, b_), t_ in zip(s.paras, s.texts)
                         if a_ >= cursor and "<hp:tbl" not in s.xml[a_:b_] and _FORM_HEAD.match(t_ or "")), len(s.xml))
        sec_end = min([at_ for s_, at_, _b, _i in located if s_ is s and at_ > cursor] + [nxt_head])
        slot = _slot_style(s, cursor)
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
            if kind == "image":
                # 작업본의 도식(그림) — BinData 에 새 항목, 매니페스트에 등록, 글자처럼 취급하는 그림 문단. 폭은 작업본 폭(pt, 1pt=100)을 본문 폭 안에서
                data = (payload or {}).get("data") or b""
                kinds = image_kind(data)
                if kinds is None or hpf is None:
                    report["images_skipped"] += 1
                    continue
                ext, media = kinds
                n = 1
                while f"image{n}" in used_bin:
                    n += 1
                bin_id = f"image{n}"
                used_bin.add(bin_id)
                added[f"BinData/{bin_id}.{ext}"] = data
                hpf = hpf.replace("</opf:manifest>", f'<opf:item id="{bin_id}" href="BinData/{bin_id}.{ext}" media-type="{media}" isEmbeded="1"/></opf:manifest>', 1)
                px = _image_px(data)
                w_pt, h_pt = float(payload.get("width_pt") or 0), float(payload.get("height_pt") or 0)
                if not h_pt and w_pt and px:
                    h_pt = w_pt * px[1] / px[0]
                org_w, org_h = (px[0] * 75, px[1] * 75) if px else (int(w_pt * 100) or s.width, int(h_pt * 100) or s.width // 2)   # 96dpi 픽셀 = 75 HU
                w_hu = int(w_pt * 100) if w_pt else org_w
                h_hu = int(h_pt * 100) if h_pt else int(w_hu * org_h / org_w)
                if w_hu > s.width:
                    w_hu, h_hu = s.width, int(h_hu * s.width / w_hu)
                pending.append(picture_xml(bin_id, next_id, w_hu, h_hu, org_w, org_h, pp, cp))
                next_id += 1
                report["images"] += 1
                put_any = True
                continue
            if kind == "text":
                for line in split_lines(str(payload)):
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
                    lp, lc = para_styles.pick(line) or slot or (pp, cp)
                    mk = marker_of(line)
                    if mk and len(line) > _ParaStyles.SHORT:            # 두 줄로 넘어갈 부호 문단 — 둘째 줄을 내용 시작에 맞춘다
                        lp, tab_hu = hangs.hanging(lp, lc, line[:_MARK.match(line).end()].strip())
                        pending.append(marker_paragraph_xml(line, lp, lc, tab_hu))
                    else:
                        pending.append(paragraph_xml(line, lp, lc))
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
                    if len(t.rows) == len(rows) and [len(r) for r in t.rows] == [len(r) for r in rows]:
                        # 모양(행·칸 수)이 같은 표는 글 유사도도 본다 — 서식 상자(1×1) 안 글을 고쳐 쓰면 칸 글자가 하나도 안 겹친다
                        # (리허설 2026-10-08: 「□ (세부)과제명: 0000」 상자를 채운 것이 새 표로 덧붙음)
                        ft = "".join(_norm(c.text) for r in t.rows for c in r)[:1500]
                        dt = "".join(_norm(c) for r in rows for c in r)[:1500]
                        score += difflib.SequenceMatcher(None, ft, dt, autojunk=False).ratio() if ft and dt else 0.0
                    if score > best:
                        best, cand = score, t
                if cand is None or best < 0.4:
                    # 글을 크게 고쳐 써 칸 글자도 글 유사도도 낮은 서식 표(상자 안 뼈대를 지우고 다시 쓴 1×1 상자 등) — 이 절 안, 커서 뒤에
                    # 모양(행·칸 수)이 같은 첫 서식 표면 그 표로 본다(리허설 7: 대표과제 상자가 새 표로 덧붙음, 유사도 0.16)
                    shape = [len(r) for r in rows]
                    same = [t for t in s.tables if not t.consumed and cursor <= t.para[0] < sec_end
                            and not _BOX_RE.search(s.xml[t.para[0]:t.para[1]]) and [len(r) for r in t.rows] == shape]
                    if same:
                        cand, best = same[0], 0.4
                pairs = _map_cells(cand, rows, widths_here) if cand is not None and best >= 0.4 else None
                grown_xml = grow_table(s.xml, cand, rows) if pairs is None and cand is not None and best >= 0.4 else None
                if grown_xml:
                    # 행이 늘어난 서식 표 — 표 문단을 통째로 바꾼다(표 서식·병합·열 폭은 그대로, 빈 입력 행을 복제)
                    flush()
                    s.splices.append((cand.para[0], cand.para[1], grown_xml))
                    cand.consumed = True
                    report["tables_grown"] += 1
                    put_any = True
                    if cand.para[0] >= cursor:
                        cursor = cand.para[1]
                    continue
                if pairs:
                    flush()
                    for fc, text in pairs:
                        if fc.nested or _norm(fc.text) == _norm(text):
                            continue
                        p_open = fc.p_open or f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                        paras_xml = "".join(f'{p_open}<hp:run charPrIDRef="{fc.char_ref}"><hp:t>{xml_text(ln.strip())}</hp:t></hp:run></hp:p>'
                                            for ln in (split_lines(text) or [""]))
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
                t = table_xml(rows, pp, cp, s.width, bf, s.margin, next_id, widths_here, roles)
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
        if "<hc:" in xml and "xmlns:hc=" not in xml[:xml.find(">", xml.find("<hs:sec")) + 1]:
            xml = xml.replace("<hs:sec ", '<hs:sec xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" ', 1)   # 그림의 hc: 요소용 선언
        if remove_boxes:
            xml, n = _remove_boxes(xml)
            report["boxes_removed"] += n
            changed = changed or n > 0
        if changed:
            xml, n = strip_linesegs(xml)
            report["linesegs_removed"] += n
            replaced[s.name] = xml.encode("utf-8")
            report["sections_changed"].append(s.name)
    if hangs.changed:
        # 내어쓰기 모양을 등록한 header — 앞서 테두리를 덧붙였으면 그 위에(같은 header 문자열을 이어 쓴다)
        base = replaced.get("Contents/header.xml")
        head_now = base.decode("utf-8") if base else header
        merged = head_now
        for body in re.findall(r'<hh:paraPr\b[^>]*\bid="(?:%s)"[^>]*>.*?</hh:paraPr>' % "|".join(hangs.cache.values()), hangs.header, re.S):
            close = merged.find("</hh:paraProperties>")
            merged = merged[:close] + body + merged[close:]
        cnt = re.search(r'(<hh:paraProperties\b[^>]*\bitemCnt=")(\d+)(")', merged)
        if cnt:
            merged = merged[:cnt.start(2)] + str(int(cnt.group(2)) + len(hangs.cache)) + merged[cnt.end(2):]
        replaced["Contents/header.xml"] = merged.encode("utf-8")
    if added:
        replaced["Contents/content.hpf"] = hpf.encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_patched(src, out, replaced, added)
    report["checks"] = check(out)
    return report
