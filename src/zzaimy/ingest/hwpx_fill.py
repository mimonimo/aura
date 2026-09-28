"""원본 한글 서식(hwpx)에 내용을 채운다 — 서식 보존 채우기(ADR-0035).

참조·협업은 구글독스 작업본에서 하고, 최종본은 원본 서식에 그 내용을 넣어 만든다(사용자 확정 2026-09-28). kordoc patch 는 블록 추가를
못 하므로(v1) 우리 hwpx 계층에서 직접: 절 제목 문단(또는 그 뒤의 【작성방법】 상자) 뒤에 문단·표를 끼워 넣는다.

kordoc(roundtrip/patcher·source-map·zip-patch·table-insert)에서 흡수한 원리 — 흐름이 아니라 기술만:
- 구역 XML 을 다시 직렬화하지 않고 글자열 위치에 끼워 넣는다(바이트 보존). 손대지 않은 문단은 한 글자도 안 바뀐다.
- 글이 바뀐 구역은 줄 배치 캐시(hp:linesegarray)를 전부 지운다 — 한글이 다시 계산하고, 변조 경고·옛 줄배치 렌더를 막는다.
- ZIP 은 원본 항목 순서·압축 방식을 그대로 두고 바뀐 항목만 다시 쓴다(mimetype 첫 항목·무압축 규약이 자동 보존).
- 새 표의 id 는 문서 전체 숫자 id 최댓값 다음부터(충돌 없음). 표 테두리는 그 구역 표들이 가장 많이 쓰는 borderFill 을 승계하고,
  표가 하나도 없는 서식이면 실선 borderFill 하나를 header 에 추가(itemCnt 갱신)한다.

원칙(특정 서식에 맞추지 않는다):
- 문단 서식은 문서의 기본 스타일('바탕글', style id 0)의 문단·글자 모양 참조를 빌린다 — 서식의 글꼴·크기·줄 간격이 그대로.
- 절 제목은 번호와 제목 글자로 찾는다(section_context 와 같은 잣대). 없는 절은 건너뛰고 보고한다.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

from zzaimy.app import section_context as sc

_P_OPEN = re.compile(r"<hp:p\b[^>]*>")
_P_CLOSE = "</hp:p>"
_T = re.compile(r"<hp:t>([^<]*)</hp:t>")
_SEC_RE = re.compile(r"Contents/section(\d+)\.xml$")
_LINESEG = re.compile(r"<(\w+:)?linesegarray\b[^>]*?(?:/>|>.*?</\1linesegarray>)", re.S)
_NUM_ID = re.compile(r"\bid(?:Ref)?=\"(\d{1,10})\"")
_BOX_RE = re.compile(r"【\s*(작성방법|증빙자료|작성\s*가이드|작성\s*요령)\s*】")


def _top_level_paragraphs(xml: str) -> list[tuple[int, int]]:
    """구역 XML 의 최상위 문단 범위 [(start, end)] — 표 안 문단은 겹침으로 건너뛴다."""
    out: list[tuple[int, int]] = []
    pos = 0
    n = len(xml)
    while True:
        m = _P_OPEN.search(xml, pos)
        if not m:
            break
        start = m.start()
        depth = 0
        i = start
        while i < n:
            nxt_open = _P_OPEN.search(xml, i)
            nxt_close = xml.find(_P_CLOSE, i)
            if nxt_close < 0:
                i = n
                break
            if nxt_open and nxt_open.start() < nxt_close:
                depth += 1
                i = nxt_open.end()
            else:
                depth -= 1
                i = nxt_close + len(_P_CLOSE)
                if depth == 0:
                    break
        out.append((start, i))
        pos = i
    return out


def _para_text(block: str) -> str:
    """문단 글(표 안 글 제외) — 첫 표 태그 앞까지의 hp:t 만."""
    cut = block.find("<hp:tbl")
    head = block if cut < 0 else block[:cut]
    return " ".join(" ".join(_T.findall(head)).split())


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


def table_xml(rows: list[list[str]], pp: str, cp: str, width_hu: int, bf: str, margin: str, table_id: int) -> str:
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    if not n_rows or not n_cols:
        return ""
    col_w = max(int(width_hu / n_cols), 1000)
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
                       f'<hp:cellSz width="{col_w}" height="{row_h}"/>{margin}</hp:tc>')
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    total_w = col_w * n_cols
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


def fill(src: Path | str, bodies: list[dict], out: Path | str, remove_boxes: bool = False) -> dict:
    """bodies = [{"heading": 절 제목, "items": [("text", 글) | ("table", 행렬), ...]}] 를 원본 서식에 넣어 out 에 저장.
    돌려주는 것: {"filled": [제목…], "skipped": [제목…], "paragraphs": n, "tables": n, "boxes_removed": n, "linesegs_removed": n,
    "sections_changed": [항목 이름…]}"""
    src, out = Path(src), Path(out)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        header = z.read("Contents/header.xml").decode("utf-8")
        sections = {n: z.read(n).decode("utf-8") for n in names if _SEC_RE.search(n)}
    pp, cp = _default_refs(header)
    solid = _solid_border_ids(header)
    next_id = _max_numeric_id([header, *sections.values()]) + 1
    report = {"filled": [], "skipped": [], "paragraphs": 0, "tables": 0, "boxes_removed": 0,
              "linesegs_removed": 0, "sections_changed": []}
    pending = list(bodies)
    replaced: dict[str, bytes] = {}
    injected_bf: str | None = None
    for name in sorted(sections, key=lambda n: int(_SEC_RE.search(n).group(1))):
        xml = sections[name]
        width = _text_width_hu(xml)
        bf, margin = _table_template(xml, solid)
        paras = _top_level_paragraphs(xml)
        texts = [_para_text(xml[a:b]) for a, b in paras]
        inserts: list[tuple[int, str, dict]] = []
        for body in list(pending):
            idx = next((i for i, t in enumerate(texts) if t and _heading_matches(t, body["heading"])), None)
            if idx is None:
                continue
            at = paras[idx][1]
            # 바로 뒤 문단이 【작성방법】 상자(표)면 그 뒤에
            if idx + 1 < len(paras):
                nxt = xml[paras[idx + 1][0]:paras[idx + 1][1]]
                if "<hp:tbl" in nxt and _BOX_RE.search(nxt):
                    at = paras[idx + 1][1]
            chunks: list[str] = []
            for kind, payload in body.get("items", []):
                if kind == "text":
                    for line in str(payload).split("\n"):
                        if line.strip():
                            chunks.append(paragraph_xml(line.strip(), pp, cp))
                            report["paragraphs"] += 1
                elif kind == "table" and payload:
                    if bf is None:
                        if injected_bf is None:
                            header, injected_bf = _inject_border_fill(header)
                            replaced["Contents/header.xml"] = header.encode("utf-8")
                        bf = injected_bf
                    t = table_xml([[str(c) for c in r] for r in payload], pp, cp, width, bf, margin, next_id)
                    if t:
                        chunks.append(t)
                        report["tables"] += 1
                        next_id += 1
            if chunks:
                inserts.append((at, "".join(chunks), body))
            pending.remove(body)
        # 뒤에서부터 끼워 넣어 앞 위치가 밀리지 않게 — 원문 조각은 그대로 잇는다
        for at, chunk, body in sorted(inserts, key=lambda x: -x[0]):
            xml = xml[:at] + chunk + xml[at:]
        report["filled"] += [body["heading"] for at, chunk, body in sorted(inserts, key=lambda x: x[0])]   # 문서 순서로 보고
        changed = bool(inserts)
        if remove_boxes:
            xml, n = _remove_boxes(xml)
            report["boxes_removed"] += n
            changed = changed or n > 0
        if changed:
            xml, n = strip_linesegs(xml)
            report["linesegs_removed"] += n
            replaced[name] = xml.encode("utf-8")
            report["sections_changed"].append(name)
    report["skipped"] = [b["heading"] for b in pending]
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_patched(src, out, replaced)
    return report
