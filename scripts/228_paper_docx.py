#!/usr/bin/env python3
"""논문 원고(docs/paper/논문/*.md) → 워드(docx) 완성본. 학교 캡스톤 논문 양식(논문_템플릿.docx, 작년도 논문)의 틀을 따른다.

양식(템플릿에서 읽은 값, docs/paper/논문-양식-가이드.md):
- A4, 여백 위·아래 25.4mm · 왼쪽·오른쪽 30mm · 머리말 15mm · 꼬리말 17.5mm, 글꼴 맑은 고딕.
- 장 제목 14pt 굵게(줄 간격 1.5) · 절 제목 12pt 굵게(1.5) · 본문 10pt 양쪽 맞춤 줄 간격 1.15 첫 줄 들여쓰기 10pt.
- 그림·표 캡션은 그림·표 아래 가운데 「[그림 n-m] 제목」, 표는 선 있는 격자(머리줄 회색).
- 표지(제목·영문 부제·팀명·팀원) → 목차 → 그림 목차 → 표 목차 → 1~6장 → 참고 문헌 → 초록 → 팀원 소개 → 부록.
- 머리말 오른쪽 줄인 논문 제목, 꼬리말 쪽 번호와 학과. 표지·목차 쪽에는 쪽 번호를 두지 않고 본문 1쪽부터 센다.
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
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
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
TITLE_EN = "(AURA: Developing an sLLM and an Agent for Automating University Administrative Work)"
SHORT = _ph.META["short"]
TEAM = "AURA"
MEMBERS = "서원준  형예진  은수인  짜투바카테베"
DEPT = "영남이공대학교 " + _ph.META["dept"]
YEAR = _ph.META["year"]
BODY_PT, CH_PT, SEC_PT = 10, 14, 12
MAX_IMG_CM = 15.0

CHAPTERS = ["01-서론.md", "02-관련-연구.md", "03-환경-설계.md", "04-구현.md", "05-평가.md", "06-결론.md",
            "99-참고-문헌.md", "97-초록.md", "98-팀원-소개.md"]
APPENDIX = "부록-사용-설명서.md"
_CAP = re.compile(r"^\s*(?:-\s*)?\[(그림|표)\s*(\d+|부록)-(\d+)\]\s*(.*)$")
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
        pf.keep_with_next = True
    if text:
        _inline(p, text, size=size, bold=bold, mono=mono)
    return p


_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*")


def _inline(p, text, *, size=BODY_PT, bold=False, mono=False):
    """`코드` 는 고정폭, **굵게** 는 굵게(원고에는 거의 없다). 나머지는 그대로."""
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            _font(p.add_run(text[pos:m.start()]), size, bold, mono=mono)
        if m.group(1) is not None:
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


def _heading(doc, text, level):
    """제목 — 워드 「제목 n」 스타일을 양식 모양(14·12pt 굵게, 줄 간격 1.5)으로 바꿔 쓴다(목차 필드가 이 스타일을 모은다)."""
    p = doc.add_paragraph(style=f"Heading {min(level, 3)}")
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pf.line_spacing = 1.5
    pf.space_before = Pt(12 if level == 1 else 8)
    pf.space_after = Pt(4)
    pf.keep_with_next = True
    _font(p.add_run(text), CH_PT if level == 1 else SEC_PT if level <= 3 else 11, True, color="000000")
    return p


def _style_headings(doc):
    for lv, size in ((1, CH_PT), (2, SEC_PT), (3, SEC_PT)):
        st = doc.styles[f"Heading {lv}"]
        st.font.name, st.font.size, st.font.bold = FONT, Pt(size), True
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
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
def _table(doc, rows):
    ncol = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=ncol)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, r in enumerate(rows):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.1
            p.paragraph_format.space_after = Pt(0)
            txt = r[j] if j < len(r) else ""
            if i == 0:
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _shade(cell._tc, "E5E5E5")
                _inline(p, txt, size=9, bold=True)
            else:
                short = len(txt) <= 12
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER if short else WD_ALIGN_PARAGRAPH.LEFT
                _inline(p, txt, size=9)
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
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        width = min(MAX_IMG_CM, w / 96 * 2.54)
        if h / max(w, 1) * width > 20:                    # 아주 긴 그림은 높이 20cm 안으로
            width = 20 * w / h
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
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            out.append(("code", code))
            i = j + 1
            continue
        if not s or s == "---":
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
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


def render(doc, text: str, missing: list, counts: dict, first_chapter_break: bool):
    pend_table = None
    for kind, val in blocks(text):
        if kind == "h":
            level, t = val
            if level == 1:
                if first_chapter_break:
                    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
                first_chapter_break = True
            _heading(doc, t, level)
            counts["h"] += 1
        elif kind == "p":
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
            for ln in val or [""]:
                p = _para(doc, ln if ln else " ", size=8, indent_pt=0, left_cm=0.4, after=0, spacing=1.0, mono=True,
                          align=WD_ALIGN_PARAGRAPH.LEFT)
                _shade(p, "F5F5F5")
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


def _hf(section, *, header_text, footer_text, page=True):
    section.header.is_linked_to_previous = False
    section.footer.is_linked_to_previous = False
    hp = section.header.paragraphs[0]
    hp.text = ""
    hp.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if header_text:
        _font(hp.add_run(header_text), 9, color="555555")
    fp = section.footer.paragraphs[0]
    fp.text = ""
    fp.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if page:
        _page_field(fp)
        _font(fp.add_run("        " + footer_text), 9, color="555555")
    elif footer_text:
        _font(fp.add_run(footer_text), 9, color="555555")


def _page_setup(section):
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(25.4)
    section.left_margin = section.right_margin = Mm(30)
    section.header_distance, section.footer_distance = Mm(15), Mm(17.5)


def _restart_numbering(section, start=1):
    sp = section._sectPr
    pg = sp.find(qn("w:pgNumType"))
    if pg is None:
        pg = OxmlElement("w:pgNumType")
        sp.append(pg)
    pg.set(qn("w:start"), str(start))


FIGS: dict = {}


def build(out: Path, appendix: bool = True) -> dict:
    global FIGS
    FIGS = _ph.figure_files(SRC / "그림-표-목록.md", ROOT)
    doc = Document()
    _style_headings(doc)
    sec0 = doc.sections[0]
    _page_setup(sec0)
    _hf(sec0, header_text="", footer_text="", page=False)
    # 표지
    for _ in range(6):
        _para(doc, "", indent_pt=0)
    _para(doc, f"{YEAR} {_ph.META['dept']} 캡스톤디자인 졸업프로젝트 논문", size=12, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=24)
    _para(doc, TITLE, size=24, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=8, spacing=1.2)
    _para(doc, TITLE_EN, size=13, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=60)
    _para(doc, f"[{TEAM}]", size=14, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=6)
    _para(doc, MEMBERS, size=13, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=6)
    _para(doc, DEPT, size=12, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0)
    # 목차·그림 목차·표 목차
    for title, instr in (("목차", 'TOC \\o "1-3" \\h \\z \\u'), ("그림 목차", 'TOC \\h \\z \\t "그림 캡션,1"'),
                         ("표 목차", 'TOC \\h \\z \\t "표 캡션,1"')):
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        _para(doc, title, size=CH_PT, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent_pt=0, after=10)
        _field(_para(doc, "", indent_pt=0), instr)
    # 본문 구역 — 머리말·꼬리말, 쪽 번호 1부터
    sec1 = doc.add_section(WD_SECTION.NEW_PAGE)
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
        if name == "97-초록.md":
            text = re.sub(r"^# 초록", "# 초록", text, flags=re.M)
        if name == APPENDIX:
            text = re.sub(r"^# (.*)$", r"# 부록. \1", text, count=1, flags=re.M)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "paper" / "AURA-논문.docx")
    ap.add_argument("--no-appendix", action="store_true")
    a = ap.parse_args(argv)
    c = build(a.out, appendix=not a.no_appendix)
    print({k: v for k, v in c.items() if k != "missing_images"})
    if c["missing_images"]:
        print("그림 파일 없음:", c["missing_images"][:20])
    print("→", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
