#!/usr/bin/env python3
"""논문 원고(docs/paper/논문/*.md) → 워드(docx) 완성본. 학교 캡스톤 논문 양식(논문_템플릿.docx, 작년도 논문)의 틀을 따른다.

양식(템플릿에서 읽은 값, docs/paper/논문-양식-가이드.md):
- A4, 여백 위·아래 25.4mm · 왼쪽·오른쪽 30mm · 머리말 15mm · 꼬리말 17.5mm, 글꼴 맑은 고딕.
- 장 제목 14pt 굵게(줄 간격 1.5) · 절 제목 12pt 굵게(1.5) · 본문 10pt 양쪽 맞춤 줄 간격 1.15 첫 줄 들여쓰기 10pt.
- 그림·표 캡션은 그림·표 아래 가운데 「[그림 n-m] 제목」, 표는 선 있는 격자(머리줄 회색).
- 표지(제목·영문 부제·팀명·팀원) → 목차 → 그림 목차 → 표 목차 → 1~6장 → 참고 문헌 → 초록 → 팀원 소개 → 부록.
- 머리말 오른쪽 줄인 논문 제목, 꼬리말 가운데 쪽 번호·오른쪽 학교와 학과. 표지 쪽은 머리말·꼬리말이 없고, 목차 쪽은 머리말·꼬리말(학과)만 있으며 쪽 번호는 본문 1쪽부터 센다.
- 목차·그림 목차·표 목차는 워드 필드(TOC)로 넣는다 — 파일을 열면 「필드 업데이트」 를 묻고, 쪽 번호가 채워진다.

원고 꼴(scripts/214_paper_write.py 와 같은 규칙): # 장, ## 절, ### 소절, #### 네 번째 단계, 「[그림 n-m] 캡션 (그림 경로)」 줄은 그림+캡션,
「[표 n-m] 캡션」 줄은 바로 앞 표의 캡션, ``` 코드, > 참고 상자, - · 1. 목록, <!-- --> 메모는 뺀다, --- 는 뺀다.

사용: python scripts/228_paper_docx.py [--out docs/paper/AURA-논문.docx] [--no-appendix]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "paper" / "논문"
FONT = "맑은 고딕"
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))
from zzaimy.app import paper_hwp as _ph  # noqa: E402  — 한글 논문 문서와 같은 제목·학과(두 파일이 같게)
TITLE = _ph.META["title"]
TITLE_EN = "(AURA: Development of an sLLM and an Agent for University Administrative Automation)"
SHORT = _ph.META["short"]
TEAM = "AURA"
MEMBERS = "서원준  형예진  은수인  짜투바카테베"
DEPT = "영남이공대학교 " + _ph.META["dept"]
YEAR = _ph.META["year"]
BODY_PT, CH_PT, SEC_PT = 10, 14, 12
MAX_IMG_CM = 15.0
LOGO_W_CM, LOGO_H_CM = 4.0, 2.0

CHAPTERS = ["01-서론.md", "02-관련-연구.md", "03-환경-설계.md", "04-구현.md", "05-평가.md", "06-결론.md",
            "99-참고-문헌.md", "97-초록.md", "98-팀원-소개.md"]
APPENDIX = "부록-사용-설명서.md"
_CAP = re.compile(r"^\s*(?:-\s*)?\[(그림|표)\s*(\d+|부록|A)-(\d+)\]\s*(.*)$")
_IMG_PATH = re.compile(r"docs/images/[^\s,()]+?\.(?:png|jpe?g|gif|svg)", re.I)
_IMG_PAREN = re.compile(r"\s*\([^()]*docs/images/[^()]*\)\s*$")
_PENDING = re.compile(r"\s*\((?:캡처 필요|작성 필요)[^)]*\)\s*$")


# ── 글꼴·문단 ──
def _font(run, size=None, bold=None, color=None, mono=False):
    f = "D2Coding" if mono else FONT
    run.font.name = f
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    for k in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rf.set(qn(k), f)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _para(doc, text="", *, size=BODY_PT, bold=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, spacing=1.15, indent_pt=10.0,
          left_cm=0.0, before=0, after=4, mono=False, style=None, keep_next=False):
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = align
    pf.line_spacing = spacing
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    if indent_pt:
        pf.first_line_indent = Pt(indent_pt)
    if left_cm:
        pf.left_indent = Cm(left_cm)
    if keep_next:
        pf.keep_with_next = True                        # 코드 제목 줄처럼 다음 것과 떨어지면 안 되는 문단
    if text:
        _inline(p, text, size=size, bold=bold, mono=mono)
    return p


_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\[\^([^\]]+)\]")


def _inline(p, text, *, size=BODY_PT, bold=False, mono=False):
    """`코드` 는 고정폭, **굵게** 는 굵게(원고에는 거의 없다). 나머지는 그대로."""
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            _font(p.add_run(text[pos:m.start()]), size, bold, mono=mono)
        if m.group(3) is not None:
            _footnote(p.part.document if hasattr(p.part, "document") else None, p, m.group(3).strip())
        elif m.group(1) is not None:
            _font(p.add_run(m.group(1)), size - 0.5, bold, mono=True)
        else:
            _font(p.add_run(m.group(2)), size, True, mono=mono)
        pos = m.end()
    if pos < len(text):
        _font(p.add_run(text[pos:]), size, bold, mono=mono)


def _shade(p_or_cell, fill="F2F2F2"):
    el = p_or_cell._element if hasattr(p_or_cell, "_element") else p_or_cell
    pr = el.get_or_add_pPr() if el.tag == qn("w:p") else el.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


HEAD_FILL = "FFFFCC"                                     # 템플릿 표·코드 표 머리줄 칠(연노랑, 11-16)


def _borders(tbl, inside_h=True):
    tblPr = tbl._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:val"), "nil" if (side == "insideH" and not inside_h) else "single")
        e.set(qn("w:sz"), "4"); e.set(qn("w:space"), "0"); e.set(qn("w:color"), "000000")
        b.append(e)
    tblPr.append(b)


def _code_table(doc, title, lines):
    """템플릿 꼴 코드 표 — 맨 위 제목 줄(연노랑), 왼쪽 줄 번호 칸, 오른쪽 코드 칸. 가로 안쪽 선은 없앤다."""
    lines = lines or [""]
    t = doc.add_table(rows=len(lines) + 1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    _borders(t, inside_h=False)
    t.columns[0].width, t.columns[1].width = Mm(9), Mm(141)   # 표 격자 너비도(LibreOffice·워드 모두 칸 너비를 따르게)
    head = t.cell(0, 0).merge(t.cell(0, 1))
    head.text = ""
    hp = head.paragraphs[0]
    hp.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hp.paragraph_format.space_after = Pt(0)
    # 제목 줄은 머리줄로 반복(tblHeader) — 표 칸 「다음 문단과 함께」 는 워드 편집 기호 ■ 가 찍혀 뺐다(11-17)
    _th = t.rows[0]._tr.get_or_add_trPr(); _h = OxmlElement("w:tblHeader"); _h.set(qn("w:val"), "true"); _th.append(_h)
    _font(hp.add_run(title or "코드"), 9)
    _shade(head._tc, HEAD_FILL)
    # 제목 줄 아래 선만 긋는다
    tcPr = head._tc.get_or_add_tcPr(); tb = OxmlElement("w:tcBorders"); bt = OxmlElement("w:bottom")
    for k, v in (("val", "single"), ("sz", "4"), ("space", "0"), ("color", "000000")):
        bt.set(qn(f"w:{k}"), v)
    tb.append(bt); tcPr.append(tb)
    for i, ln in enumerate(lines, 1):
        for j, txt in ((0, str(i)), (1, ln if ln.strip() else " ")):
            c = t.cell(i, j)
            c.width = Mm(9) if j == 0 else Mm(141)
            c.text = ""
            p = c.paragraphs[0]
            pf = p.paragraph_format
            pf.space_after = Pt(0); pf.line_spacing = 1.0
            pf.alignment = WD_ALIGN_PARAGRAPH.RIGHT if j == 0 else WD_ALIGN_PARAGRAPH.LEFT
            _font(p.add_run(txt.replace("\t", "    ")), 8, mono=(j == 1), color="555555" if j == 0 else None)
        tr = t.rows[i]._tr.get_or_add_trPr(); cs = OxmlElement("w:cantSplit"); cs.set(qn("w:val"), "true"); tr.append(cs)
    _para(doc, "", indent_pt=0, after=2)
    return t


# ── 각주 — 원고 「[^키]」 를 워드 각주로(쪽 아래 작은 글씨), 정의는 「[^키]: 설명」 줄 ──
FOOT = {"part": None, "n": 0, "defs": {}}
_FOOT_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"


def _foot_root(doc):
    if FOOT["part"] is None:
        from docx.opc.part import XmlPart
        from docx.opc.packuri import PackURI
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from docx.oxml import parse_xml
        xml = ('<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:footnote w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:separator/></w:r></w:p></w:footnote>'
               '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
               '</w:footnotes>')
        part = XmlPart(PackURI("/word/footnotes.xml"), _FOOT_CT, parse_xml(xml), doc.part.package)
        doc.part.relate_to(part, RT.FOOTNOTES)
        FOOT["part"] = part
    return FOOT["part"].element


def _footnote(doc, p, key):
    text = FOOT["defs"].get(key)
    if not text:
        return
    root = _foot_root(doc)
    FOOT["n"] += 1
    n = FOOT["n"]
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    from docx.oxml import parse_xml
    from xml.sax.saxutils import escape
    font = '<w:rFonts w:ascii="맑은 고딕" w:eastAsia="맑은 고딕" w:hAnsi="맑은 고딕"/>'
    root.append(parse_xml(
        f'<w:footnote xmlns:w="{W}" w:id="{n}"><w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>'
        f'<w:r><w:rPr>{font}<w:vertAlign w:val="superscript"/><w:sz w:val="16"/></w:rPr><w:footnoteRef/></w:r>'
        f'<w:r><w:rPr>{font}<w:sz w:val="16"/></w:rPr><w:t xml:space="preserve"> {escape(text)}</w:t></w:r></w:p></w:footnote>'))
    r = p.add_run()
    rPr = r._r.get_or_add_rPr()
    va = OxmlElement("w:vertAlign"); va.set(qn("w:val"), "superscript"); rPr.append(va)
    ref = OxmlElement("w:footnoteReference"); ref.set(qn("w:id"), str(n)); r._r.append(ref)


_FOOT_DEF = re.compile(r"^\[\^([^\]]+)\]:\s*(.+)$", re.M)


def take_footnotes(text: str) -> str:
    """원고에서 각주 정의 줄을 떼어 FOOT["defs"] 에 담고, 정의를 뺀 글을 돌려준다."""
    for m in _FOOT_DEF.finditer(text):
        FOOT["defs"][m.group(1).strip()] = m.group(2).strip()
    return _FOOT_DEF.sub("", text)


def _heading(doc, text, level, page_before: bool = False):
    if page_before:
        _new_page_section(doc)
    """제목 — 워드 「제목 n」 스타일을 양식 모양(14·12pt 굵게, 줄 간격 1.5)으로 바꿔 쓴다(목차 필드가 이 스타일을 모은다)."""
    p = doc.add_paragraph(style=f"Heading {min(level, 3)}")
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pf.line_spacing = 1.5
    pf.space_before = Pt(12 if level == 1 else 8)
    pf.space_after = Pt(4)
    # 제목은 다음 문단과 함께(쪽 끝에 제목만 남지 않게, 11-18). 「앞에서 쪽 나눔」 은 쓰지 않는다 — 장 앞 쪽 나눔은 구역 나눔으로
    pf.keep_with_next = True
    pf.page_break_before = False
    _font(p.add_run(text), CH_PT if level == 1 else SEC_PT if level == 2 else 11, True, color="000000")   # 템플릿: 장 14·절 12·소절 11pt
    return p


def _new_page_section(doc):
    """새 쪽에서 시작하는 구역 — 머리말·꼬리말·쪽 번호는 앞 구역을 그대로 잇는다(편집 기호 ■ 없이 장을 새 쪽에서)."""
    from docx.enum.section import WD_SECTION
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    prev = doc.sections[-2]
    sec.page_width, sec.page_height = prev.page_width, prev.page_height
    sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin = prev.left_margin, prev.right_margin, prev.top_margin, prev.bottom_margin
    sec.header_distance, sec.footer_distance = prev.header_distance, prev.footer_distance
    sec.different_first_page_header_footer = False      # 표지 구역의 「첫 쪽 다름」 이 복사되면 장 첫 쪽마다 머리말·쪽 번호·학과가 빈다(11-18)
    sec.header.is_linked_to_previous = True
    sec.footer.is_linked_to_previous = True
    pg = sec._sectPr.find(qn("w:pgNumType"))
    if pg is not None:
        sec._sectPr.remove(pg)                          # 쪽 번호를 1로 다시 세지 않게 — 앞 구역에서 이어 센다


def _style_headings(doc):
    for lv, size in ((1, CH_PT), (2, SEC_PT), (3, 11)):
        st = doc.styles[f"Heading {lv}"]
        st.paragraph_format.keep_with_next = True       # 제목은 다음 문단과 함께 — 쪽 끝에 제목만 남고 내용이 다음 쪽으로 가던 것(11-18 1.1.2)
        st.font.name, st.font.size, st.font.bold = FONT, Pt(size), True
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    for other in doc.styles:                            # 기본 스타일(캡션 등)에 걸린 것은 끈다 — 제목만 다음 문단과 함께
        try:
            if other.name.startswith("Heading"):
                continue
            if other.paragraph_format.keep_with_next:
                other.paragraph_format.keep_with_next = False
        except (AttributeError, ValueError):
            pass
    for name in ("Caption",):
        st = doc.styles[name]
        st.font.name, st.font.size, st.font.bold, st.font.italic = FONT, Pt(9), True, False
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = FONT, Pt(BODY_PT)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)


def _caption(doc, kind, text):
    """그림·표 캡션 — 「그림 캡션」·「표 캡션」 스타일(그림 목차·표 목차 필드가 이 스타일을 모은다)."""
    name = "그림 캡션" if kind == "그림" else "표 캡션"
    if name not in [s.name for s in doc.styles]:
        st = doc.styles.add_style(name, 1)
        st.base_style = doc.styles["Caption"]
    p = doc.add_paragraph(style=name)
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    _font(p.add_run(text), 9, True)
    return p


def _field(p, instr, placeholder="(목차는 워드에서 「필드 업데이트」 를 누르면 채워진다)"):
    r = p.add_run()
    for tag, val in (("w:fldChar", "begin"),):
        e = OxmlElement(tag)
        e.set(qn("w:fldCharType"), val)
        r._element.append(e)
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    r._element.append(it)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    r._element.append(sep)
    t = p.add_run(placeholder)
    _font(t, 9, color="888888")
    end_r = p.add_run()
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    end_r._element.append(e)


def _page_field(p):
    _field(p, "PAGE", "1")


# ── 표·그림 ──
IMG_MAX_H_CM = 12.5                                     # 그림 높이 상한(cm) — 도식 글자 9pt 를 지키는 값(11-17: 줄이면 글자가 작아진다)
KEEP_ROWS = 4                                           # 이보다 짧은 표만 통째로 다음 쪽으로 — 15행까지 묶으니 앞 쪽에 큰 빈칸이 생겼다(11-16)
_NUMISH = re.compile(r"(?=.*\d).{1,24}")                    # 숫자가 든 짧은 값(수치·건수·시간)


def _keep_lead_in(doc, limit: int = 160) -> None:
    """표·그림 바로 앞의 짧은 소개 문단(「…는 [표 A-16]과 같다」)을 표·그림과 같은 쪽에 — 소개 글만 쪽 끝에 남지 않게(11-18).
    긴 문단은 묶지 않는다(쪽 하나를 통째로 밀어 큰 빈자리가 생기므로)."""
    body = doc.element.body
    last = body[-2] if len(body) >= 2 and body[-1].tag == qn("w:sectPr") else body[-1]
    if last.tag != qn("w:p"):
        return
    text = "".join(t.text or "" for t in last.iter(qn("w:t")))
    if 0 < len(text.strip()) <= limit:
        from docx.text.paragraph import Paragraph
        Paragraph(last, doc._body).paragraph_format.keep_with_next = True


def _table(doc, rows):
    _keep_lead_in(doc)
    ncol = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=ncol)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 열마다 정렬을 하나로 — 칸마다 글 길이로 정하면 같은 열 안에서 가운데·왼쪽이 섞였다(11-15). 모든 칸이 짧거나 수치면 가운데, 아니면 왼쪽
    def _plain(v):
        return re.sub(r"[*`]", "", v or "").strip()
    col_center = []
    for j in range(ncol):
        vals = [_plain(r[j]) for r in rows[1:] if j < len(r) and _plain(r[j])]
        col_center.append(bool(vals) and all(len(v) <= 12 or _NUMISH.fullmatch(v) for v in vals))
    # 열 너비를 글 양에 맞춘다 — 같은 너비면 긴 글 열에서 한두 글자만 다음 줄로 넘어가 보기 싫었다(11-16).
    # 열마다 무게 = 칸 글 길이의 평균(긴 칸 쪽으로 조금 더), 바닥은 머리말·가장 긴 낱말이 한 줄에 들어갈 너비
    CH_MM = 3.3                                          # 9pt 한글 한 글자 폭(여백 포함 어림)
    PAD = 4.0
    text_mm = 150.0
    # 웹 표의 자동 배치와 같은 방법(11-18 사용자: 표가 최적화 안 된 느낌) — 열마다 한 줄로 쓸 때의 너비(가장 긴 칸)와 줄여도 되는
    # 바닥(가장 긴 낱말, 4자 이상)을 잰다. 모두 한 줄로 들어가면 그 비율대로 본문 폭을 채우고, 넘치면 바닥을 먼저 주고 남은 폭은
    # 「한 줄 너비 − 바닥」 이 큰 열(긴 글 열)에 나눠 준다. 짧은 수치 열이 넓고 긴 글 열이 네 줄로 접히던 것을 막는다
    nat, low = [], []
    for j in range(ncol):
        cells_ = [_plain(r[j]) for r in rows if j < len(r)]
        longest = max([len(v) for v in cells_ if v] or [1])
        word = max([len(w) for v in cells_ for w in v.split()] or [1])
        nat.append(longest * CH_MM + PAD)
        low.append(min(max(word, 4) * CH_MM + PAD, text_mm * 0.4))
    if sum(nat) <= text_mm:
        widths = [w * text_mm / sum(nat) for w in nat]
    else:
        free = max(text_mm - sum(low), 0)
        extra = [max(nat[j] - low[j], 0) for j in range(ncol)]
        widths = [low[j] + (free * extra[j] / sum(extra) if sum(extra) else free / ncol) for j in range(ncol)]
    if sum(widths) > text_mm:                            # 바닥의 합이 본문 폭을 넘으면 비율대로 줄인다(11-17 표 3-7 이 오른쪽 여백 밖으로)
        widths = [w * text_mm / sum(widths) for w in widths]
    t.autofit = False
    tpr = t._tbl.tblPr
    lay = OxmlElement("w:tblLayout"); lay.set(qn("w:type"), "fixed"); tpr.append(lay)   # 워드가 글 길이로 다시 넓히지 않게
    tw = OxmlElement("w:tblW"); tw.set(qn("w:w"), str(int(sum(widths) * 56.7))); tw.set(qn("w:type"), "dxa")
    for old in tpr.findall(qn("w:tblW")):
        tpr.remove(old)
    tpr.append(tw)
    for j in range(ncol):
        t.columns[j].width = Mm(widths[j])
        for i in range(len(rows)):
            t.cell(i, j).width = Mm(widths[j])
    keep_all = len(rows) <= KEEP_ROWS                  # 짧은 표는 쪽에서 나누지 않고 통째로(캡션까지) 다음 쪽으로
    for i, r in enumerate(rows):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.text = ""
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER   # 한 행 안에서 짧은 칸이 위에 붙어 들쭉날쭉하던 것(11-16)
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.1
            p.paragraph_format.space_after = Pt(0)
            txt = r[j] if j < len(r) else ""
            if i == 0:
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _shade(cell._tc, HEAD_FILL)
                _inline(p, txt, size=9, bold=True)
            else:
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER if col_center[j] else WD_ALIGN_PARAGRAPH.LEFT
                _inline(p, txt, size=9)
            if i == 0 and len(rows) > 1:
                # 머리줄 칸만 「다음 문단과 함께」 — 머리줄만 쪽 끝에 홀로 남던 것(11-17 사용자, 표 2-1·3-3·3-6). ■ 는 편집 기호로만 보이고 인쇄되지 않는다
                p.paragraph_format.keep_with_next = True
            # 그 밖의 표 칸에는 「다음 문단과 함께」 를 걸지 않는다 — 워드 편집 기호에서 표 둘레마다 점이 찍혀 어지러웠다(11-16 사용자).
            # 행이 두 쪽에 잘리지 않게(cantSplit)와 쪽이 넘어가면 머리줄 다시(tblHeader)만 둔다
        trpr = t.rows[i]._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit")                  # 행 하나가 두 쪽에 걸쳐 잘리지 않게
        cs.set(qn("w:val"), "true")
        trpr.append(cs)
    # 머리줄은 쪽이 넘어가면 다시
    trpr = t.rows[0]._tr.get_or_add_trPr()
    h = OxmlElement("w:tblHeader")
    h.set(qn("w:val"), "true")
    trpr.append(h)
    return t


def _md_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _image(doc, path: Path, missing: list):
    if not path.exists():
        missing.append(str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path))
        p = _para(doc, f"(그림 파일 없음: {path.name})", align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0)
        return p
    if path.suffix.lower() == ".svg":
        png = path.with_suffix(".png")
        if png.exists():
            path = png
        else:
            missing.append(str(path))
            return _para(doc, f"(SVG 그림: {path.name})", align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0)
    _keep_lead_in(doc)
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True       # 그림은 아래 캡션과 같은 쪽에(그림만 남고 캡션이 다음 쪽으로 가지 않게)
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        width = min(MAX_IMG_CM, w / 96 * 2.54)
        if h / max(w, 1) * width > IMG_MAX_H_CM:          # 그림 높이 상한 — 20cm 면 남은 자리에 못 들어가 통째로 다음 쪽으로 넘어가며
            width = IMG_MAX_H_CM * w / h                  # 앞 쪽 아래가 크게 비었다(11-16 사용자: 「사진을 위로 못 올리나」)
        if "/logos/" in str(path).replace("\\", "/"):     # 로고는 작게 — 4cm × 2cm 안(한글 쪽 paper_hwp.LOGO_MAX_W·H 와 같은 값)
            width = min(width, LOGO_W_CM, LOGO_H_CM * w / max(h, 1))
        p.add_run().add_picture(str(path), width=Cm(width))
    except Exception as e:
        missing.append(f"{path} ({type(e).__name__})")
    return p


# ── 원고 읽기 ──
def blocks(text: str):
    """md → (갈래, 값) 차례. 갈래: h(level,text) p(text) list(items) quote(text) code(lines) table(rows) cap(kind, num, text, img)."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = text.split("\n")
    i, out, para = 0, [], []
    spots: dict = {}

    def flush():
        if para:
            out.append(("p", " ".join(x.strip() for x in para)))
            para.clear()
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush()
            j = i + 1
            code = []
            info = s[3:].strip().split(None, 1)              # 「```python 에이전트 단계 관문」 — 둘째 낱말부터가 코드 표 제목
            title_ = info[1] if len(info) > 1 else ""
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            out.append(("code", (title_, code)))
            i = j + 1
            continue
        if not s or s == "---":
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m and m.group(2).strip().startswith("이 장의 그림"):   # 원고 메모 절(그림·표 자리 목록·수치 출처) — 한글 문서와 같이 넣지 않는다
            flush()
            lv = len(m.group(1))
            i += 1
            while i < len(lines) and not (re.match(r"^(#{1,4})\s", lines[i].strip()) and len(re.match(r"^(#+)", lines[i].strip()).group(1)) <= lv):
                i += 1
            continue
        if m:
            flush()
            out.append(("h", (len(m.group(1)), m.group(2).strip())))
            i += 1
            continue
        if s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                r = lines[i].strip()
                if not re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", r):
                    rows.append(_md_row(r))
                i += 1
            out.append(("table", rows))
            continue
        cm = _CAP.match(s)
        if cm and not s.startswith("-") or (cm and s.startswith("- [")):
            flush()
            body = cm.group(4)
            num = f"{cm.group(2)}-{cm.group(3)}"
            if body.startswith("자리:"):                   # 「[그림 5-1] 자리: 경로 …」 — 다음 캡션 줄의 그림 파일
                spots[(cm.group(1), num)] = _IMG_PATH.findall(body)
                i += 1
                continue
            imgs = _IMG_PATH.findall(body) if _IMG_PAREN.search(body) else []
            body = _IMG_PAREN.sub("", body).rstrip()
            body = _PENDING.sub("", body).rstrip()
            if not body.strip():                           # 「[그림 5-4] (그림 경로)」 처럼 그림만 적은 줄 — 다음 캡션 줄의 그림
                spots[(cm.group(1), num)] = imgs
                i += 1
                continue
            imgs = imgs or spots.pop((cm.group(1), num), [])
            img = imgs or None
            # 본문 문장 속 「[표 4-1] 은 …」 같은 줄은 캡션이 아니다(뒤에 조사·서술)
            if re.match(r"^(은|는|이|가|을|를|과|와|에|의|처럼|에서|로)\b", body) or body.endswith(("다.", "다")):
                para.append(s)
                i += 1
                continue
            out.append(("cap", (cm.group(1), num, body, img, s.startswith("-"))))
            i += 1
            continue
        if s.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(("quote", " ".join(q)))
            continue
        lm = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", ln)
        if lm:
            flush()
            items = []
            while i < len(lines):
                lm2 = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", lines[i])
                if lm2:
                    items.append([len(lm2.group(1)), lm2.group(2), lm2.group(3).strip()])
                    i += 1
                elif lines[i].strip() and lines[i].startswith(" ") and items:
                    items[-1][2] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append(("list", items))
            continue
        para.append(s)
        i += 1
    flush()
    return out


# 팀원 사진 — 개인 사진이라 저장소에 넣지 않는다(git 밖 data/paper/team/<차례>.<이름>.jpeg). 없으면 사진 칸을 비운다
TEAM_PHOTOS = ROOT / "data" / "paper" / "team"


def _team_people(text: str) -> list[dict]:
    """원고의 「이름 : …　　전공 : …　　학번 : …」 줄과 「졸업 프로젝트 역할 : …」 줄 → [{이름, 전공, 학번, 역할}]."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("이름 :"):
            parts = dict((k.strip(), v.strip()) for k, v in (x.split(":", 1) for x in re.split(r"\s{2,}|　+", s) if ":" in x))
            out.append({"이름": parts.get("이름", ""), "전공": parts.get("전공", ""), "학번": parts.get("학번", ""), "역할": ""})
        elif s.startswith("졸업 프로젝트 역할 :") and out:
            out[-1]["역할"] = s.split(":", 1)[1].strip()
    return out


def _render_team(doc, text: str, first_chapter_break: bool) -> bool:
    _heading(doc, "팀원 소개", 1, page_before=bool(first_chapter_break))
    photos = sorted(TEAM_PHOTOS.glob("*.jp*g")) + sorted(TEAM_PHOTOS.glob("*.png")) if TEAM_PHOTOS.is_dir() else []
    for person in _team_people(text):
        bare = re.sub(r"\s*\(.*?\)", "", person["이름"]).strip()
        photo = next((p for p in photos if bare and bare in p.stem), None)
        t = doc.add_table(rows=1, cols=2)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        w = [Cm(3.8), Cm(11.2)]
        for j in range(2):
            t.cell(0, j).width = w[j]
            t.columns[j].width = w[j]                           # 표 격자 너비도 — 칸 너비만 주면 리브레·한글이 반반으로 나눴다
        lay = OxmlElement("w:tblLayout"); lay.set(qn("w:type"), "fixed"); t._tbl.tblPr.append(lay)
        tr = t.rows[0]._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit"); cs.set(qn("w:val"), "true"); tr.append(cs)
        # 사진은 칸을 꽉 채운다(11-18 사용자: 사진을 칸 배경처럼) — 사진 칸의 안쪽 여백을 없애고 사진 너비 = 칸 너비, 칸 높이 = 사진 높이
        tcpr = t.cell(0, 0)._tc.get_or_add_tcPr()
        mar = OxmlElement("w:tcMar")
        for side in ("top", "left", "bottom", "right"):
            e = OxmlElement(f"w:{side}"); e.set(qn("w:w"), "0"); e.set(qn("w:type"), "dxa"); mar.append(e)
        tcpr.append(mar)
        left = t.cell(0, 0).paragraphs[0]
        left.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        left.paragraph_format.line_spacing = 1.0                     # 본문 줄 간격(고정값)이면 그림이 줄 높이에 잘린다
        left.paragraph_format.space_before = left.paragraph_format.space_after = Pt(0)
        left.paragraph_format.first_line_indent = Pt(0)
        if photo:
            left.add_run().add_picture(str(photo), width=w[0])
        right = t.cell(0, 1)
        right.paragraphs[0].text = ""
        # 사람마다 테두리 상자, 역할은 머리말 없이 내용만(11-16 사용자: 「졸업 프로젝트 역할:」 은 군더더기)
        _borders(t)
        for j in range(2):
            t.cell(0, j).vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        lines = [("이   름", person["이름"]), ("전   공", person["전공"]), ("학   번", person["학번"]), ("", person["역할"])]
        first = True
        for k, v in lines:
            p = right.paragraphs[0] if first else right.add_paragraph()
            first = False
            p.paragraph_format.space_after = Pt(2)
            if k:
                _font(p.add_run(f"{k} : {v}"), 10.5)
            elif v:
                p.paragraph_format.space_before = Pt(6)
                _font(p.add_run(v), 10.5)
        _para(doc, "", indent_pt=0, after=10)
    return True


REF_MODE = False


def render(doc, text: str, missing: list, counts: dict, first_chapter_break: bool):
    pend_table = None
    for kind, val in blocks(text):
        if kind == "h":
            level, t = val
            brk = False
            if level == 1:
                brk = bool(first_chapter_break)        # 장 제목 앞 쪽 나눔은 제목 문단에(빈 문단 쪽 나눔은 쪽 끝에서 빈 쪽을 만든다)
                first_chapter_break = True
            _heading(doc, t, level, page_before=brk)
            counts["h"] += 1
        elif kind == "p":
            if REF_MODE and re.match(r"^\[\d+\]", val):     # 참고 문헌 — 작은 글씨, 번호 뒤로 내어쓰기, 촘촘하게(11-16 「너무 주루룩」)
                _para(doc, val, size=9, align=WD_ALIGN_PARAGRAPH.LEFT, indent_pt=-24, left_cm=0.85, after=2, spacing=1.0)
            elif REF_MODE and re.fullmatch(r"\*\*[^*]+\*\*", val.strip()):   # 분야 머리
                _para(doc, val, size=10, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, indent_pt=0, before=8, after=3, keep_next=True)
            else:
                _para(doc, val)
            counts["p"] += 1
        elif kind == "list":
            for ind, mark, t in val:
                bullet = "•" if mark in "-*" else mark.rstrip(".)") + ")"
                _para(doc, f"{bullet} {t}", indent_pt=0, left_cm=0.6 + 0.5 * (ind // 2), after=2)
                counts["p"] += 1
        elif kind == "quote":
            p = _para(doc, val, size=9.5, indent_pt=0, left_cm=0.4)
            _shade(p, "F2F2F2")
        elif kind == "code":
            _code_table(doc, *val)
        elif kind == "table":
            pend_table = _table(doc, val)
            counts["table"] += 1
        elif kind == "cap":
            k, num, body, img, listed = val
            if listed:                                          # 장 끝 「그림 모음」 목록 줄 — 캡션이 아니라 목록
                _para(doc, f"• [{k} {num}] {body}", indent_pt=0, left_cm=0.6, after=2)
                continue
            if k == "그림":
                listed_imgs = FIGS.get(num)                     # 그림·표 목록의 「자료」 가 정본(한글 논문 문서와 같은 그림)
                if listed_imgs:
                    img = [str(p_.relative_to(ROOT)) for p_ in listed_imgs]
                if img:
                    for one in img:
                        _image(doc, ROOT / one, missing)
                        counts["img"] += 1
                else:
                    _para(doc, "(그림 자리)", align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, size=9)
                    counts["img_pending"] += 1
            _caption(doc, k, f"[{k} {num}] {body}")
            counts["cap_" + k] += 1
            pend_table = None
    return first_chapter_break


def _style_toc(doc):
    """목차 줄 간격을 템플릿처럼 좁게(줄 간격 1.0, 문단 뒤 0), 1단계는 굵게."""
    from docx.enum.style import WD_STYLE_TYPE
    for lv in (1, 2, 3):
        name = f"toc {lv}"
        try:
            st = doc.styles[name]
        except KeyError:
            st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.paragraph_format.space_after = Pt(0)
        st.paragraph_format.space_before = Pt(0)
        st.paragraph_format.line_spacing = 1.0
        st.paragraph_format.left_indent = Pt(10 * (lv - 1))
        st.font.size = Pt(10)
        st.font.bold = True                                # 템플릿 목차 줄은 모두 굵게


_PPR_AFTER_BDR = ("shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
                  "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
                  "suppressOverlap", "jc", "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle",
                  "rPr", "sectPr", "pPrChange")


def _rule(p, side: str):
    """문단 테두리 한 변(머리말 아래·꼬리말 위 선) — w:pBdr 는 pPr 안 정해진 자리에 둬야 워드가 그린다(맨 끝에 붙이면 무시됐다, 11-15)."""
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    e = OxmlElement(f"w:{side}")
    for k, v in (("val", "single"), ("sz", "24"), ("space", "4"), ("color", "000000")):   # 템플릿 선: 검정 3pt(11-16)
        e.set(qn(f"w:{k}"), v)
    bdr.append(e)
    after = next((c for c in ppr if c.tag.split("}")[-1] in _PPR_AFTER_BDR), None)
    if after is not None:
        after.addprevious(bdr)
    else:
        ppr.append(bdr)


def _hf(section, *, header_text, footer_text, page=True):
    section.header.is_linked_to_previous = False
    section.footer.is_linked_to_previous = False
    hp = section.header.paragraphs[0]
    hp.text = ""
    hp.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if header_text:
        _rule(hp, "bottom")                                # 머리말 아래·꼬리말 위 가는 선(템플릿 틀)
    if header_text:
        _font(hp.add_run(header_text), 9, color="262626")
    fp = section.footer.paragraphs[0]
    fp.text = ""
    if footer_text or page:
        _rule(fp, "top")
    # 쪽 번호는 가운데, 학교·학과는 오른쪽(한글 논문 문서 paper_hwp.PAGE_NUM_POS·FOOTER_ALIGN 과 같은 배치, 11-15)
    width = section.page_width - section.left_margin - section.right_margin
    tabs = fp.paragraph_format.tab_stops
    for t in list(fp.style.paragraph_format.tab_stops):  # 「바닥글」 스타일의 기본 탭(가운데 4680·오른쪽 9360)을 지운다 — 남아 있으면 학과가 그 가운데 탭에 걸렸다(11-15)
        tabs.add_tab_stop(t.position, WD_TAB_ALIGNMENT.CLEAR)
    tabs.add_tab_stop(width // 2, WD_TAB_ALIGNMENT.CENTER)
    tabs.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
    if page:
        fp.add_run("\t")
        _page_field(fp)
    if footer_text:
        _font(fp.add_run("\t" if page else "\t\t"), 9)
        _font(fp.add_run(footer_text), 9, color="555555")


def _page_setup(section):
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(25.4)
    section.left_margin = section.right_margin = Mm(30)
    section.header_distance, section.footer_distance = Mm(15), Mm(10)   # 꼬리말은 쪽 아래 끝 가까이(11-12, 한글 쪽과 같은 자리)


def _restart_numbering(section, start=1):
    sp = section._sectPr
    pg = sp.find(qn("w:pgNumType"))
    if pg is None:
        pg = OxmlElement("w:pgNumType")
        sp.append(pg)
    pg.set(qn("w:start"), str(start))


FIGS: dict = {}


def _split_title(t: str) -> list[str]:
    """표지 제목을 뜻 단위 두 줄로 — 「위한」·「for」 뒤(앞)에서. 못 찾으면 한 줄."""
    m = re.search(r"(위한)\s+", t)
    if m:
        return [t[:m.end(1)], t[m.end():]]
    m = re.search(r"\s+(for\b)", t)
    if m:
        return [t[:m.start()], t[m.start(1):]]
    return [t]


def _cover_lines(doc, lines: list[str], size: float) -> None:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.line_spacing = 1.2
    pf.space_after = Pt(8)
    for i, ln in enumerate(lines):
        r = p.add_run(ln)
        _font(r, size, bold=True)
        if i < len(lines) - 1:
            r.add_break()


def _no_autospace(doc) -> None:
    """한글·영문·숫자 사이 자동 간격을 끈다 — 원고 「AURA를」·「2~4장」 이 워드에서 「AURA 를」·「2~4 장」 처럼 벌어져 보였다(11-16).
    문서 기본 문단 속성(docDefaults/pPrDefault)에 autoSpaceDE·autoSpaceDN=0."""
    st = doc.styles.element
    dd = st.find(qn("w:docDefaults"))
    if dd is None:
        dd = OxmlElement("w:docDefaults"); st.insert(0, dd)
    pd = dd.find(qn("w:pPrDefault"))
    if pd is None:
        pd = OxmlElement("w:pPrDefault"); dd.append(pd)
    ppr = pd.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr"); pd.append(ppr)
    for tag in ("w:autoSpaceDE", "w:autoSpaceDN"):
        el = ppr.find(qn(tag))
        if el is None:
            el = OxmlElement(tag); ppr.append(el)
        el.set(qn("w:val"), "0")


def build(out: Path, appendix: bool = True) -> dict:
    global FIGS
    FIGS = _ph.figure_files(SRC / "그림-표-목록.md", ROOT)
    doc = Document()
    _no_autospace(doc)
    _style_headings(doc)
    _style_toc(doc)
    sec0 = doc.sections[0]
    _page_setup(sec0)
    # 템플릿처럼 표지 쪽만 비우고(첫 쪽 다름) 목차 쪽에도 머리말(제목·아래 줄)·꼬리말(학과·위 줄)을 둔다 — 쪽 번호는 본문부터(11-16)
    sec0.different_first_page_header_footer = True
    _hf(sec0, header_text=SHORT, footer_text=DEPT, page=False)
    # 표지
    # 템플릿 표지(11-15): 국문 제목 25pt 굵게, 영문 부제(괄호) 굵게(길어서 20pt), 팀명 20pt 굵게 가운데, 팀원 18pt 가운데 — 머리 줄·학과 줄 없음
    # 제목이 여러 줄이라 양쪽 정렬이면 낱말 사이가 벌어지고, 빈 줄이 많으면 팀명·팀원이 둘째 쪽으로 밀렸다(11-16) — 왼쪽 정렬, 빈 줄을 줄여 한 쪽에
    for _ in range(3):
        _para(doc, "", indent_pt=0)
    # 가운데 정렬, 뜻 단위로 줄을 나눈다(11-16 사용자: 왼쪽 정렬이 어색) — 「…을 위한 / …」, 「… Agent / for …」
    _cover_lines(doc, _split_title(TITLE), 25)
    _cover_lines(doc, _split_title(TITLE_EN), 17)        # 영문은 길어 20pt 면 세 줄로 접혔다 — 두 줄에 들게
    for _ in range(10):
        _para(doc, "", indent_pt=0)
    _para(doc, TEAM, size=20, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=6)
    _para(doc, MEMBERS, size=18, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0)
    # 목차·그림 목차·표 목차
    for title, instr in (("목차", 'TOC \\o "1-2" \\h \\z \\u'), ("그림 목차", 'TOC \\h \\z \\t "그림 캡션,1"'),
                         ("표 목차", 'TOC \\h \\z \\t "표 캡션,1"')):
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        _para(doc, title, size=18, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, indent_pt=0, after=10)   # 템플릿: 「목차」 18pt 굵게 왼쪽
        _field(_para(doc, "", indent_pt=0), instr)
    # 본문 구역 — 머리말·꼬리말, 쪽 번호 1부터
    sec1 = doc.add_section(WD_SECTION.NEW_PAGE)
    sec1.different_first_page_header_footer = False      # 본문은 첫 쪽부터 쪽 번호·학과
    _page_setup(sec1)
    _hf(sec1, header_text=SHORT, footer_text=DEPT)
    _restart_numbering(sec1, 1)
    # 캡션 스타일을 먼저 만들어 둔다(목차 필드가 이름으로 찾는다)
    for name in ("그림 캡션", "표 캡션"):
        if name not in [s.name for s in doc.styles]:
            st = doc.styles.add_style(name, 1)
            st.base_style = doc.styles["Caption"]
    counts = {"h": 0, "p": 0, "table": 0, "img": 0, "img_pending": 0, "cap_그림": 0, "cap_표": 0}
    missing: list = []
    brk = False
    files = CHAPTERS + ([APPENDIX] if appendix else [])
    for name in files:
        text = (SRC / name).read_text(encoding="utf-8")
        text = take_footnotes(text)
        global REF_MODE
        REF_MODE = name.startswith("99-")
        if name == APPENDIX:
            text = re.sub(r"^# (?!부록)(.*)$", r"# 부록. \1", text, count=1, flags=re.M)   # 원고 제목이 이미 「부록.」 이면 그대로(「부록. 부록.」 이 됐다, 11-16)
        if name == "98-팀원-소개.md" and _team_people(text):
            brk = _render_team(doc, text, brk)             # 템플릿 꼴: 사람마다 사진 왼쪽 + 이름·전공·학번·역할 오른쪽
            continue
        brk = render(doc, text, missing, counts, brk)
    # 열 때 필드(목차·쪽 번호) 업데이트를 묻게
    st = doc.settings.element
    uf = OxmlElement("w:updateFields")
    uf.set(qn("w:val"), "true")
    st.append(uf)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    counts["missing_images"] = missing
    return counts


# ── 목차 쪽 번호 미리 채우기 ── 워드를 보기 전용으로 여는 사람·제출본을 위해(11-15 사용자: 「워드만 제출」, 보기 전용이라 필드 업데이트를 저장할 수 없음).
# 쪽은 LibreOffice 로 PDF 를 만들어 제목·캡션이 처음 나오는 쪽을 읽는다(231 이 운영 서버에서 돌림). 필드는 그대로라 워드에서 업데이트하면 정확한 값으로.
def toc_entries(path: Path) -> dict:
    """완성본의 목차 항목 — {"toc": [(단계, 글)], "그림": [글], "표": [글]} (문서 차례대로)."""
    d = Document(str(path))
    out = {"toc": [], "그림": [], "표": []}
    for p in d.paragraphs:
        name = p.style.name if p.style is not None else ""
        t = p.text.strip()
        if not t:
            continue
        m = re.match(r"Heading (\d)", name)
        if m and int(m.group(1)) <= 2:
            out["toc"].append((int(m.group(1)), t))
        elif name == "그림 캡션":
            out["그림"].append(t)
        elif name == "표 캡션":
            out["표"].append(t)
    return out


def toc_pages(entries: dict, pdf_pages: list[str]) -> dict:
    """PDF 쪽 글(쪽마다 하나) → {항목 글: 쪽 번호}. 본문 1쪽은 첫 장 제목이 나오는 쪽, 찾기는 문서 차례대로 앞에서부터(목차 쪽에 같은 글이 있어도 그 뒤만)."""
    norm = lambda x: re.sub(r"[^0-9A-Za-z가-힣]", "", x)
    pages = [norm(x) for x in pdf_pages]
    first = entries["toc"][0][1] if entries["toc"] else ""
    start = next((i for i in range(len(pages)) if i > 0 and norm(first) and norm(first) in pages[i] and i > 2), None)
    if start is None:
        return {}
    found: dict = {}
    for key in ("toc", "그림", "표"):
        pos = start
        for e in entries[key]:
            t = e[1] if isinstance(e, tuple) else e
            k = norm(t)[:40]
            j = next((i for i in range(pos, len(pages)) if k and k in pages[i]), None)
            if j is not None:
                found[t] = j - start + 1
                pos = j
    return found


def fill_toc(path: Path, pages: dict) -> int:
    """목차·그림 목차·표 목차 필드의 결과 자리에 항목과 쪽 번호를 넣고 「열 때 필드 업데이트」 를 끈다 — 넣은 줄 수."""
    import copy
    d = Document(str(path))
    entries = toc_entries(path)
    width = d.sections[-1].page_width - d.sections[-1].left_margin - d.sections[-1].right_margin
    from docx.enum.text import WD_TAB_LEADER
    n = 0
    for p in list(d.paragraphs):
        instr = "".join(x.text or "" for x in p._p.iter(qn("w:instrText")))
        if "TOC" not in instr:
            continue
        kind = "그림" if "그림 캡션" in instr else "표" if "표 캡션" in instr else "toc"
        items = [(lv, t) for lv, t in entries["toc"]] if kind == "toc" else [(1, t) for t in entries[kind]]
        if not items:
            continue
        # 결과 자리의 안내 글을 지우고, 끝 표시(fldChar end)는 마지막 항목 문단으로 옮긴다
        end = None
        for r in p.runs:
            if r._element.find(qn("w:fldChar")) is not None and r._element.find(qn("w:fldChar")).get(qn("w:fldCharType")) == "end":
                end = r._element
            elif r._element.find(qn("w:fldChar")) is None and r._element.find(qn("w:instrText")) is None:
                r.text = ""
        anchor = p
        for i, (lv, t) in enumerate(items):
            q = copy.deepcopy(p._p) if False else None
            np_ = d.add_paragraph(style=f"toc {min(lv, 3)}")
            np_.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DASHES)
            _font(np_.add_run(f"{t}\t{pages.get(t, '')}"), 10, True)
            anchor._p.addnext(np_._p)
            anchor = np_
            n += 1
        if end is not None:
            end.getparent().remove(end)
            anchor._p.append(end)
    st = d.settings.element
    for e in st.findall(qn("w:updateFields")):
        st.remove(e)
    d.save(str(path))
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "paper" / "AURA-논문.docx")
    ap.add_argument("--no-appendix", action="store_true")
    ap.add_argument("--toc-pages", type=Path, help="목차 쪽 번호(JSON {항목 글: 쪽}) — 주면 목차를 미리 채우고 열 때 묻지 않게")
    ap.add_argument("--entries", type=Path, help="이미 만든 완성본의 목차 항목을 JSON 으로 내고 끝낸다(쪽 재기용)")
    ap.add_argument("--pages-from", nargs=2, type=Path, metavar=("ENTRIES_JSON", "PDF_TXT"), help="항목 JSON 과 pdftotext 글로 쪽 JSON 을 낸다")
    a = ap.parse_args(argv)
    import json as _json
    if a.entries:
        a.entries.write_text(_json.dumps(toc_entries(a.out), ensure_ascii=False), encoding="utf-8")
        return 0
    if a.pages_from:
        ent = _json.loads(a.pages_from[0].read_text(encoding="utf-8"))
        ent["toc"] = [tuple(x) for x in ent["toc"]]
        print(_json.dumps(toc_pages(ent, a.pages_from[1].read_text(encoding="utf-8").split("\f")), ensure_ascii=False))
        return 0
    c = build(a.out, appendix=not a.no_appendix)
    if a.toc_pages:
        pages = _json.loads(a.toc_pages.read_text(encoding="utf-8"))
        print("목차 미리 채움:", fill_toc(a.out, pages), "줄 · 쪽 찾음", len(pages))
    print({k: v for k, v in c.items() if k != "missing_images"})
    if c["missing_images"]:
        print("그림 파일 없음:", c["missing_images"][:20])
    print("→", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
