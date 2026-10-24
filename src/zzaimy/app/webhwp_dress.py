"""에이전트(27B) 절 쓰기의 꾸밈 — 논문 서식째 쓰기(paper_hwp)의 꾸밈 스타일을 행정 문서 절 쓰기에도.

27B 는 글만 쓴다(마크다운 몇 가지 표시). 모양은 코드가 정한다:
- 「> 」 로 시작하는 줄(이어진 줄은 한 상자) → 참고 상자(연한 바탕 1칸 표, 「팁 상자」 스타일)
- 표 바로 위·아래의 「<표> 제목」·「<표 3> 제목」·「[표 3] 제목」 줄 → 가운데·굵게 표 제목(「표 캡션」). 번호를 안 적으면
  문서 안 차례대로 매긴다(다른 절에 이미 있는 <표 N> 다음 번호)
개조식 번호(1) 가) (1))·표·도식은 그대로 webhwp_write.parse_body 가 맡는다(양식의 문단 모양을 따른다).
"""
from __future__ import annotations

import re

_BOX = re.compile(r"^\s*>\s?(.*)$")
_CAP = re.compile(r"^\s*[<\[〈]\s*표\s*(\d+(?:-\d+)?)?\s*[>\]〉]\s*(.+?)\s*$")
_HAVE = re.compile(r"<\s*표\s*(\d+)\s*>")


def _next_no(others: list[str], mine: list[str]) -> int:
    """다른 절 글과 이 절 앞쪽 글에 이미 있는 <표 N> 의 가장 큰 N + 1."""
    seen = [int(x) for t in others + mine for x in _HAVE.findall(t or "")]
    return (max(seen) if seen else 0) + 1


def dress_items(items: list[tuple[str, object]], others: list[str] | None = None) -> tuple[list[tuple[str, object]], dict[str, str]]:
    """parse_body 의 items → (상자·표 제목을 나눈 items, {글: 꾸밈 스타일 이름}). 표시가 없으면 items 그대로·빈 짝."""
    others = others or []
    out: list[tuple[str, object]] = []
    style: dict[str, str] = {}
    done: list[str] = []
    for i, (kind, val) in enumerate(items):
        if kind != "text":
            out.append((kind, val))
            continue
        lines = str(val).split("\n")
        buf: list[str] = []
        box: list[str] = []

        def flush_buf():
            if buf:
                out.append(("text", "\n".join(buf)))
                buf.clear()

        def flush_box():
            if box:
                cell = "\n".join(box).strip()
                if cell:
                    out.append(("table", [[cell]]))
                    style[re.sub(r"\s+", " ", cell).strip()] = "팁 상자"
                box.clear()
        for j, ln in enumerate(lines):
            b = _BOX.match(ln)
            if b:
                flush_buf()
                box.append(b.group(1))
                continue
            flush_box()
            c = _CAP.match(ln)
            # 표 제목은 표와 붙어 있을 때만(첫 줄이면 앞 덩이가 표, 끝 줄이면 다음 덩이가 표) — 문장 속 「<표 3> 에서 보듯」 은 글로 둔다
            near = (j == len(lines) - 1 and i + 1 < len(items) and items[i + 1][0] == "table") or \
                   (j == 0 and out and out[-1][0] == "table")
            if c and near and not re.match(r"^(은|는|이|가|을|를|의|에서|에|처럼|과|와)(\s|$)", c.group(2)):
                flush_buf()
                no = c.group(1) or str(_next_no(others, done))
                cap = f"<표 {no}> {c.group(2)}"
                done.append(cap)
                out.append(("text", cap))
                style[cap] = "표 캡션"
                continue
            buf.append(ln)
        flush_box()
        flush_buf()
    return out, style


def apply(data: bytes, put: list[str], style: dict[str, str], field: str = "") -> bytes:
    """쓴 문단(put)과 칸 첫 문단(field — 절이 표 제목으로 시작하면 제목이 칸 안 문단에 든다)에 꾸밈 스타일 — 필요한 스타일을
    header 에 두고(없으면) 입힌 뒤, 문서 전체의 표 제목 번호를 차례대로 다시 매긴다."""
    if not style:
        return data
    from zzaimy.app import paper_hwp as ph
    from xml.sax.saxutils import escape
    data, _ = ph.add_styles(data)
    style = {**style, **{escape(k): v for k, v in style.items()}}   # 문단 글은 XML 그대로 견준다(「<표 1>」 → 「&lt;표 1&gt;」)
    head = ph._field_para_id(data, field) if field else ""
    data = ph.dress_written(data, list(put) + ([head] if head else []), style)
    return renumber(data)


def renumber(data: bytes) -> bytes:
    """「표 캡션」 꾸밈이 붙은 문단의 「<표 N>」 을 문서 차례대로 1, 2, 3 … — 27B 가 적은 번호가 겹치거나, 앞 절을 나중에 써도 맞게."""
    import io
    import zipfile
    zin = zipfile.ZipFile(io.BytesIO(data))
    hdr = zin.read("Contents/header.xml").decode("utf-8")
    m = re.search(r'<hh:style\b[^>]*\bid="(\d+)"[^>]*\bname="표 캡션"', hdr)
    if not m:
        return data
    sid, n, new = m.group(1), 0, {}
    secs = sorted((x for x in zin.namelist() if re.fullmatch(r"Contents/section\d+\.xml", x)), key=lambda x: int(re.search(r"\d+", x).group(0)))

    def one(p: re.Match) -> str:
        nonlocal n
        n += 1
        return re.sub(r"&lt;표\s*\d+(?:-\d+)?&gt;", f"&lt;표 {n}&gt;", p.group(0), count=1)
    for sn in secs:
        xml = zin.read(sn).decode("utf-8")
        x2 = re.sub(rf'<hp:p\b[^>]*\bstyleIDRef="{sid}"[^>]*>(?:(?!</hp:p>).)*?&lt;표\s*\d+(?:-\d+)?&gt;', one, xml, flags=re.S)
        if x2 != xml:
            new[sn] = x2
    if not new:
        return data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()
