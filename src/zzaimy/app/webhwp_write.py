"""기안기 작업본에 쓰기(서버 쪽) — 에이전트 도구가 브라우저 없이 한글 파일(hwpx)의 누름틀 필드에 값을 넣고 절 본문(문단·표)을 쓴다.

- fields(data): 필드 목록과 지금 값 — [{"name", "kind"(절·칸·표·기타), "text"}]
- set_fields(data, {이름: 값}): 칸·표 필드 값 바꾸기(줄바꿈은 한 칸 안 줄 나눔)
- write_section(data, 이름, items, written): 절 필드가 든 문단 바로 뒤에 문단·표를 넣는다. items = [("text", 글) | ("table", 행렬)].
  앞서 같은 절에 넣은 문단(written[이름] 의 문단 id)은 지우고 새로 넣는다 — 「다시 써 줘」 가 덧붙이지 않게.
  문단·표 모양은 서식 보존 채우기(hwpx_fill)와 같은 함수로 만든다(서식의 기본 문단·글자 모양, 실선 테두리).
돌려주는 것은 새 hwpx 바이트(와 절 쓰기는 이번에 넣은 문단 id 목록). 파일 저장·판 남기기는 webhwp.save 가 한다.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

from zzaimy.app import webhwp_fields as wf
from zzaimy.ingest import hwpx_fill as hf

_SEC = re.compile(r"Contents/section\d+\.xml$")
_BEGIN = re.compile(r'<hp:ctrl><hp:fieldBegin\b[^>]*\bid="(\d+)"[^>]*type="CLICK_HERE"[^>]*\bname="([^"]*)"[^>]*>.*?</hp:fieldBegin></hp:ctrl>', re.S)


def _open(data: bytes) -> tuple[zipfile.ZipFile, dict[str, str]]:
    zin = zipfile.ZipFile(io.BytesIO(data))
    wf.check_zip(zin)
    return zin, {n: zin.read(n).decode("utf-8") for n in zin.namelist() if _SEC.search(n)}


def _pack(zin: zipfile.ZipFile, new: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def _unq(name: str) -> str:
    return name.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _kind(name: str) -> str:
    return "절" if name.startswith("절:") else "칸" if name.startswith("칸:") else "표" if re.match(r"표\d+:", name) else "기타"


def _span(xml: str, bid: str, start: int) -> tuple[int, int] | None:
    """필드 여는 ctrl 끝 ~ 닫는 ctrl 시작(그 사이가 필드 값)."""
    m = re.search(rf'<hp:ctrl><hp:fieldEnd\b[^>]*beginIDRef="{bid}"[^>]*/></hp:ctrl>', xml[start:])
    return (start, start + m.start()) if m else None


def fields(data: bytes) -> list[dict]:
    _, secs = _open(data)
    out = []
    for n in sorted(secs):
        xml = secs[n]
        for m in _BEGIN.finditer(xml):
            sp = _span(xml, m.group(1), m.end())
            text = re.sub(r"<[^>]+>", "", xml[sp[0]:sp[1]]).strip() if sp else ""
            name = _unq(m.group(2))
            out.append({"name": name, "kind": _kind(name), "text": text})
    return out


def _value_xml(text: str) -> str:
    lines = str(text or "").replace("\r", "").split("\n")
    return "<hp:t>" + "<hp:lineBreak/>".join(escape(x) for x in lines) + "</hp:t>"


def set_fields(data: bytes, values: dict[str, str]) -> tuple[bytes, list[str]]:
    """칸·표 필드 값 바꾸기 — 돌려주는 것은 (새 바이트, 넣은 필드 이름). 없는 이름은 건너뛴다."""
    zin, secs = _open(data)
    want = {k: v for k, v in values.items() if k}
    done: list[str] = []
    new = {}
    for n, xml in secs.items():
        pos, buf = 0, []
        for m in _BEGIN.finditer(xml):
            name = _unq(m.group(2))
            if name not in want or name in done:
                continue
            sp = _span(xml, m.group(1), m.end())
            if not sp:
                continue
            buf.append(xml[pos:sp[0]])
            buf.append(_value_xml(want[name]))
            pos = sp[1]
            done.append(name)
        if buf:
            buf.append(xml[pos:])
            new[n] = "".join(buf)
    return (_pack(zin, new) if new else data), done


def _para_bounds(xml: str, at: int) -> tuple[int, int] | None:
    """at 을 품은 가장 안쪽 문단 <hp:p …>…</hp:p> 의 범위."""
    for m in wf._LEAF_P.finditer(xml):
        if m.start() <= at < m.end():
            return m.start(), m.end()
    return None


def _cell_width(xml: str, at: int) -> int | None:
    """at 이 표 칸 안이면 그 칸의 폭(HWPUNIT) — 칸 안 절에 넣는 표는 칸 폭에 맞춘다."""
    tc = xml.rfind("<hp:tc", 0, at)
    end = xml.rfind("</hp:tc>", 0, at)
    if tc == -1 or end > tc:
        return None
    m = re.search(r'<hp:cellSz width="(\d+)"', xml[tc:xml.find("</hp:tc>", at) + 8])
    return int(m.group(1)) - 600 if m else None


def write_section(data: bytes, name: str, items: list[tuple[str, object]], written: dict[str, list[str]] | None = None
                  ) -> tuple[bytes, list[str]]:
    """절 필드 다음에 문단·표를 넣는다 — (새 바이트, 넣은 문단 id). 앞서 넣은 것은 지운다."""
    zin, secs = _open(data)
    header = zin.read("Contents/header.xml").decode("utf-8")
    pp, cp = hf._default_refs(header)
    solid = hf._solid_border_ids(header)
    old_ids = set((written or {}).get(name, []))
    ids = wf._Ids([header, *secs.values()])
    new: dict[str, str] = {}
    put: list[str] = []
    for n, xml in secs.items():
        hit = next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)
        if not hit:
            continue
        if old_ids:                                          # 앞서 이 절에 넣은 문단(표 문단 포함)을 지운다
            for pid in old_ids:
                m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
                if not m:
                    continue
                depth, i = 0, m.start()
                for t in re.finditer(r"<hp:p\b|</hp:p>", xml[m.start():]):
                    depth += 1 if t.group(0) != "</hp:p>" else -1
                    if depth == 0:
                        xml = xml[:m.start()] + xml[m.start() + t.end():]
                        break
            hit = next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)
        # 본문 첫 줄은 절 필드 안에 넣는다 — 빈 필드의 안내 글(「본문 입력」)이 본문 위에 남지 않게. 나머지는 그 문단 뒤에
        items = list(items)
        first = ""
        if items and items[0][0] == "text":
            lines = hf.split_lines(str(items[0][1]))
            if lines:
                first, rest = lines[0], "\n".join(lines[1:])
                items[0] = ("text", rest)
        sp = _span(xml, hit.group(1), hit.end())
        if sp:
            xml = xml[:sp[0]] + _value_xml(first) + xml[sp[1]:]
            hit = next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)
        pb = _para_bounds(xml, hit.start())
        if not pb:
            continue
        bf, margin = hf._table_template(xml, solid)
        if bf is None:
            header, bf = hf._inject_border_fill(header)
            margin = margin or '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
        width = _cell_width(xml, hit.start()) or hf._text_width_hu(xml)
        chunks = []
        for kind, val in items:
            if kind == "text":
                for line in (hf.split_lines(str(val)) if str(val).strip() else []):
                    pid = ids.new()
                    chunks.append(hf.paragraph_xml(line, pp, cp).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                    put.append(pid)
            elif kind == "table" and isinstance(val, list) and val:
                rows = [[str(c) for c in r] for r in val]
                pid = ids.new()
                chunks.append(hf.table_xml(rows, pp, cp, width, bf, margin, int(ids.new())).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                put.append(pid)
        new[n] = xml[:pb[1]] + "".join(chunks) + xml[pb[1]:]
        break
    if not new:
        raise KeyError(f"절 필드 「{name}」 을 찾지 못했습니다")
    out = _pack(zin, new)
    if header != zin.read("Contents/header.xml").decode("utf-8"):    # 테두리 모양을 새로 넣었으면 header 도 바꿔 담는다
        zin2 = zipfile.ZipFile(io.BytesIO(out))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for info in zin2.infolist():
                body = header.encode("utf-8") if info.filename == "Contents/header.xml" else zin2.read(info.filename)
                zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        out = buf.getvalue()
    return out, put


def parse_body(text: str) -> list[tuple[str, object]]:
    """에이전트가 쓴 절 본문(글 + 마크다운 표 | a | b |) → items. 표 구분 줄(|---|)은 버린다."""
    items: list[tuple[str, object]] = []
    lines = str(text or "").replace("\r", "").split("\n")
    buf: list[str] = []
    table: list[list[str]] = []

    def flush_text():
        if buf and any(x.strip() for x in buf):
            items.append(("text", "\n".join(x for x in buf if x.strip())))
        buf.clear()

    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            flush_text()
            if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", s):
                continue
            table.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if table:
            items.append(("table", table))
            table = []
        buf.append(ln)
    if table:
        items.append(("table", table))
    flush_text()
    return items
