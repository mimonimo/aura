"""옛 한글(.hwp 5.0) → DOCX 충실 변환 — pyhwp 의 구조 XML(hwp5proc xml)을 읽어 hwpx 변환기와 같은 길로 옮긴다.

pyhwp 의 HTML·ODT 출력은 표 열 폭이 줄고 글꼴·바탕이 화면용이라 독스에서 원본과 멀었다(실측 2026-09-24, 사업계획서 106쪽).
대신 pyhwp 가 풀어 주는 구조 XML(DocInfo 의 글자·문단·테두리 모양, BodyText 의 문단·표·셀·글상자·그림)을 hwpx 의 XML 모양
(hp:p·run·t·tbl·tr·tc·pic·rect·secPr)으로 바꿔 `hwpx_docx.Converter` 에 넣는다. 변환기 하나로 두 형식의 품질을 같게 한다.

구조 XML 은 크다(30MB 한글 → 104MB, 7.5분). 파일 해시로 캐시(cache/hwp5xml/)한다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

try:  # XML 폭탄·외부 개체 방어
    from defusedxml.ElementTree import parse as _parse
except Exception:  # pragma: no cover
    from xml.etree.ElementTree import parse as _parse

from zzaimy.ingest.hwpx_docx import BorderFill, CharStyle, Converter, ParaStyle, Styles

_HEADING_NAME = re.compile(r"개요\s*(\d)|outline\s*(\d)|제목|title|heading", re.I)


def _hwp5proc() -> Path | None:
    exe = Path(sys.executable).parent / "hwp5proc"
    return exe if exe.exists() else None


def _cache_dir() -> Path:
    base = Path(os.environ.get("ZZAIMY_DATA_DIR", "data/platform")).parent / "cache" / "hwp5xml"
    base.mkdir(parents=True, exist_ok=True)
    return base


def dump_xml(src: Path, timeout_s: int = 1800) -> Path:
    """구조 XML 을 만든다(캐시). hwp5proc 이 없으면 RuntimeError."""
    src = Path(src)
    h = hashlib.sha256()
    h.update(str(src.stat().st_size).encode())
    with src.open("rb") as f:
        h.update(f.read(1 << 20))
    out = _cache_dir() / f"{h.hexdigest()[:24]}.xml"
    if out.exists() and out.stat().st_size > 100:
        return out
    exe = _hwp5proc()
    if exe is None:
        raise RuntimeError("hwp5proc 이 없습니다(pyhwp)")
    tmp = out.with_suffix(".part")
    with tmp.open("wb") as fo:
        subprocess.run([str(exe), "xml", "--embedbin", str(src)], check=True, stdout=fo, stderr=subprocess.DEVNULL, timeout=timeout_s)
    tmp.replace(out)
    return out


# ---- 구조 XML → hwpx 모양 -------------------------------------------------------------------------

def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class _BinStore:
    """Converter 가 zf.read(member) 로 그림을 꺼낸다 — BinData 의 base64 를 그대로 준다."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def read(self, member: str) -> bytes:
        return self.blobs[member]                 # 형식 정리는 Converter.picture 의 normalize_image 가 한다


def _load_styles(docinfo: ET.Element) -> tuple[Styles, dict[str, bytes]]:
    st = Styles()
    faces: list[str] = []
    ko_fonts = 0
    idm = docinfo.find(".//IdMappings")
    if idm is not None:
        ko_fonts = _int(idm.get("ko-fonts"))
    for f in docinfo.iter("FaceName"):
        faces.append(f.get("name") or "")
    ko_faces = faces[:ko_fonts] if ko_fonts else faces
    for i, cs_el in enumerate(docinfo.iter("CharShape")):
        cs = CharStyle()
        cs.size_pt = _int(cs_el.get("basesize"), 1000) / 100.0
        cs.bold = cs_el.get("bold") == "1"
        cs.italic = cs_el.get("italic") == "1"
        # pyhwp 는 이 파일의 글자 모양 대부분을 underline="line_through" underline-style="15"(정의 안 된 값)로 낸다 — 플래그 해석이
        # 어긋난 것이라(실측 2026-09-24: 5,787개 런에 밑줄) 값과 모양이 둘 다 정상일 때만 밑줄로 본다
        u_kind = (cs_el.get("underline") or "none").lower()
        u_style = (cs_el.get("underline-style") or "solid").lower()
        cs.underline = u_kind in ("bottom", "top", "center") and u_style in ("solid", "dashed", "dotted", "dash_dot", "dash_dot_dot",
                                                                           "long_dashed", "large_dotted", "double")
        color = (cs_el.get("text-color") or "#000000").lstrip("#")
        cs.color = color if re.fullmatch(r"[0-9A-Fa-f]{6}", color) else "000000"
        ff = cs_el.find("FontFace")
        if ff is not None:
            idx = _int(ff.get("ko"))
            if 0 <= idx < len(ko_faces):
                cs.font = ko_faces[idx]
        st.chars[str(i)] = cs
    for i, ps_el in enumerate(docinfo.iter("ParaShape")):
        ps = ParaStyle()
        ps.align = {"both": "JUSTIFY", "left": "LEFT", "right": "RIGHT", "center": "CENTER", "distribute": "DISTRIBUTE",
                    "distribute-space": "DISTRIBUTE"}.get(ps_el.get("align") or "both", "JUSTIFY")
        ps.left_hu = _int(ps_el.get("doubled-margin-left")) // 2
        ps.indent_hu = _int(ps_el.get("indent")) // 2
        ps.before_hu = _int(ps_el.get("doubled-margin-top")) // 2
        ps.after_hu = _int(ps_el.get("doubled-margin-bottom")) // 2
        if (ps_el.get("linespacing-type") or "ratio") == "ratio":
            ps.line_pct = _int(ps_el.get("linespacing"))
        if (ps_el.get("head-shape") or "none") == "outline":
            ps.outline_level = _int(ps_el.get("level")) + 1
        st.paras[str(i)] = ps
    for i, bf_el in enumerate(docinfo.iter("BorderFill"), start=1):     # borderfill-id 는 1부터
        bf = BorderFill()
        for b in bf_el.findall("Border"):
            side = b.get("attribute-name") or ""
            if side in ("left", "right", "top", "bottom"):
                kind = "NONE" if (b.get("stroke-type") or "none") == "none" else "SOLID"
                try:
                    mm = float((b.get("width") or "0.12mm").replace("mm", ""))
                except ValueError:
                    mm = 0.12
                bf.sides[side] = (kind, mm)
        fp = bf_el.find("FillColorPattern")
        if fp is not None and (bf_el.get("fillflags") or "00000000") != "00000000":
            face = (fp.get("background-color") or "").lstrip("#")
            if re.fullmatch(r"[0-9A-Fa-f]{6}", face) and face.upper() not in ("FFFFFF", "000000"):
                bf.fill = face.upper()
        st.borders[str(i)] = bf
    for i, s_el in enumerate(docinfo.iter("Style")):
        name = f"{s_el.get('local-name') or ''} {s_el.get('name') or ''}"
        m = _HEADING_NAME.search(name)
        if m:
            lvl = m.group(1) or m.group(2)
            st.heading_styles[str(i)] = int(lvl) if lvl else 1
    blobs: dict[str, bytes] = {}
    for i, bd in enumerate(docinfo.iter("BinData"), start=1):
        emb = bd.find("BinDataEmbedding")
        if emb is None or not (emb.text or "").strip():
            continue
        try:
            blobs[str(i)] = base64.b64decode("".join((emb.text or "").split()))
        except Exception:
            continue
    return st, blobs


def _translate_paragraph(p_el: ET.Element) -> ET.Element:
    p = ET.Element("p", {"paraPrIDRef": p_el.get("parashape-id") or "0", "styleIDRef": p_el.get("style-id") or "0",
                         "pageBreak": "1" if p_el.get("new-page") == "1" else "0"})
    run = None
    cur_t = None
    last_char = "0"

    def new_run(char_id: str):
        nonlocal run, cur_t
        run = ET.SubElement(p, "run", {"charPrIDRef": char_id})
        cur_t = None
        return run

    for seg in p_el.findall("LineSeg"):
        for node in seg:
            tag = node.tag
            if tag == "SectionDef":
                r = new_run(last_char)
                sec = ET.SubElement(r, "secPr")
                pd = node.find(".//PageDef")
                if pd is not None:
                    pp = ET.SubElement(sec, "pagePr", {"width": pd.get("width") or "0", "height": pd.get("height") or "0"})
                    ET.SubElement(pp, "margin", {"left": pd.get("left-offset") or "0", "right": pd.get("right-offset") or "0",
                                                 "top": pd.get("top-offset") or "0", "bottom": pd.get("bottom-offset") or "0",
                                                 "header": pd.get("header-offset") or "0", "footer": pd.get("footer-offset") or "0",
                                                 "gutter": pd.get("bookbinding-offset") or "0"})
            elif tag == "Text":
                char_id = node.get("charshape-id") or last_char
                last_char = char_id
                if run is None or run.get("charPrIDRef") != char_id or cur_t is None:
                    r = new_run(char_id)
                    cur_t = ET.SubElement(r, "t")
                    cur_t.text = node.text or ""
                else:
                    _append_text(cur_t, node.text or "")
            elif tag == "ControlChar":
                name = node.get("name") or ""
                if name == "LINE_BREAK":
                    if cur_t is None:
                        r = new_run(last_char)
                        cur_t = ET.SubElement(r, "t")
                    ET.SubElement(cur_t, "lineBreak")
                elif name == "TAB":
                    if cur_t is None:
                        r = new_run(last_char)
                        cur_t = ET.SubElement(r, "t")
                    ET.SubElement(cur_t, "tab")
            elif tag == "TableControl":
                r = new_run(last_char)
                r.append(_translate_table(node))
            elif tag == "GShapeObjectControl":
                r = new_run(last_char)
                _translate_shape(node, r)
    return p


def _append_text(t: ET.Element, text: str) -> None:
    kids = list(t)
    if kids:
        kids[-1].tail = (kids[-1].tail or "") + text
    else:
        t.text = (t.text or "") + text


def _translate_table(tc_el: ET.Element) -> ET.Element:
    body = tc_el.find("TableBody")
    tbl = ET.Element("tbl", {"rowCnt": (body.get("rows") if body is not None else "0") or "0",
                             "colCnt": (body.get("cols") if body is not None else "0") or "0",
                             "borderFillIDRef": (body.get("borderfill-id") if body is not None else "") or ""})
    ET.SubElement(tbl, "sz", {"width": tc_el.get("width") or "0", "height": tc_el.get("height") or "0"})
    ET.SubElement(tbl, "pos", {"treatAsChar": "1" if tc_el.get("inline") == "1" else "0"})
    for row in (body.findall("TableRow") if body is not None else []):
        tr = ET.SubElement(tbl, "tr")
        for cell in row.findall("TableCell"):
            tc = ET.SubElement(tr, "tc", {"borderFillIDRef": cell.get("borderfill-id") or ""})
            sub = ET.SubElement(tc, "subList", {"vertAlign": (cell.get("valign") or "top").upper().replace("MIDDLE", "CENTER")})
            for para in cell.findall("Paragraph"):
                sub.append(_translate_paragraph(para))
            ET.SubElement(tc, "cellAddr", {"rowAddr": cell.get("row") or "0", "colAddr": cell.get("col") or "0"})
            ET.SubElement(tc, "cellSpan", {"rowSpan": cell.get("rowspan") or "1", "colSpan": cell.get("colspan") or "1"})
            ET.SubElement(tc, "cellSz", {"width": cell.get("width") or "0", "height": cell.get("height") or "0"})
    return tbl


def _translate_shape(g_el: ET.Element, run: ET.Element) -> None:
    """그림 → pic, 글상자(사각형+글) → rect, 여러 도형 묶음 → container."""
    inline = "1" if g_el.get("inline") == "1" else "0"
    width, height = g_el.get("width") or "0", g_el.get("height") or "0"
    comps = g_el.findall("ShapeComponent")
    if not comps:
        return
    holder = run
    if len(comps) > 1 or any(c.findall("ShapeComponent") for c in comps):
        holder = ET.SubElement(run, "container")
    for comp in comps:
        _translate_component(comp, holder, inline, width, height)


def _translate_component(comp: ET.Element, holder: ET.Element, inline: str, width: str, height: str) -> None:
    pic = comp.find("ShapePicture")
    if pic is not None:
        info = pic.find("PictureInfo")
        p = ET.SubElement(holder, "pic")
        ET.SubElement(p, "pos", {"treatAsChar": inline})
        ET.SubElement(p, "curSz", {"width": comp.get("width") or width, "height": comp.get("height") or height})
        ET.SubElement(p, "orgSz", {"width": comp.get("initial-width") or width, "height": comp.get("initial-height") or height})
        ET.SubElement(p, "img", {"binaryItemIDRef": (info.get("bindata-id") if info is not None else "") or ""})
        return
    nested = comp.findall("ShapeComponent")
    if nested:
        c = ET.SubElement(holder, "container")
        for n in nested:
            _translate_component(n, c, inline, width, height)
        return
    tpl = comp.find("TextboxParagraphList")
    if tpl is not None:
        rect = ET.SubElement(holder, "rect")
        border = comp.find(".//BorderLine")
        style = "NONE" if border is None or (border.get("stroke") or "none") == "none" else "SOLID"
        ET.SubElement(rect, "lineShape", {"style": style})
        draw = ET.SubElement(rect, "drawText")
        sub = ET.SubElement(draw, "subList", {"vertAlign": (tpl.get("valign") or "top").upper().replace("MIDDLE", "CENTER")})
        for para in tpl.findall("Paragraph"):
            sub.append(_translate_paragraph(para))


def convert_xml(xml_path: Path) -> tuple[bytes, dict]:
    """구조 XML → (docx 바이트, 통계)."""
    tree = _parse(str(xml_path))
    root = tree.getroot()
    docinfo = root.find("DocInfo")
    if docinfo is None:
        raise ValueError("DocInfo 가 없는 구조 XML")
    styles, blobs = _load_styles(docinfo)
    conv = Converter(styles, _BinStore(blobs), {k: k for k in blobs})
    body = root.find("BodyText")
    # 실물 구조(실측 2026-09-24): HwpDoc/BodyText/SectionDef/ColumnSet/Paragraph. 쪽 설정(PageDef)은 SectionDef 아래에 있다.
    sections = (body.findall("SectionDef") + body.findall("Section")) if body is not None else []
    for sec in sections:
        paras = sec.findall("Paragraph") + [p for cs in sec.findall("ColumnSet") for p in cs.findall("Paragraph")]
        pd = sec.find("PageDef")
        if pd is None:
            pd = next((x for x in sec.iter("PageDef")), None)
        first = True
        for p_el in paras:
            p = _translate_paragraph(p_el)
            if first and pd is not None and p.find(".//secPr") is None:
                r = ET.Element("run", {"charPrIDRef": "0"})
                secpr = ET.SubElement(r, "secPr")
                pp = ET.SubElement(secpr, "pagePr", {"width": pd.get("width") or "0", "height": pd.get("height") or "0"})
                ET.SubElement(pp, "margin", {"left": pd.get("left-offset") or "0", "right": pd.get("right-offset") or "0",
                                             "top": pd.get("top-offset") or "0", "bottom": pd.get("bottom-offset") or "0",
                                             "header": pd.get("header-offset") or "0", "footer": pd.get("footer-offset") or "0",
                                             "gutter": pd.get("bookbinding-offset") or "0"})
                p.insert(0, r)
            first = False
            conv.paragraph(p, conv.doc)
    from docx.shared import Pt

    conv.doc.styles["Normal"].font.size = Pt(10)
    buf = io.BytesIO()
    conv.doc.save(buf)
    return buf.getvalue(), conv.stats


def convert(src: Path | str) -> tuple[bytes, dict]:
    """.hwp → (docx 바이트, 통계). pyhwp 가 없으면 RuntimeError."""
    return convert_xml(dump_xml(Path(src)))
