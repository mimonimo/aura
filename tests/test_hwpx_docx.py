"""HWPX → DOCX 충실 변환 — 글자 모양·문단 정렬·표(병합·테두리·바탕색·중첩)·쪽 설정이 옮겨진다."""

import io
import zipfile

from docx import Document
from docx.oxml.ns import qn

from zzaimy.ingest import hwpx_docx

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">
<hh:refList>
<hh:fontfaces><hh:fontface lang="HANGUL"><hh:font id="0" face="맑은 고딕" type="TTF"/></hh:fontface></hh:fontfaces>
<hh:borderFills itemCnt="3">
 <hh:borderFill id="1"><hh:leftBorder type="NONE" width="0.1 mm"/><hh:rightBorder type="NONE" width="0.1 mm"/><hh:topBorder type="NONE" width="0.1 mm"/><hh:bottomBorder type="NONE" width="0.1 mm"/></hh:borderFill>
 <hh:borderFill id="2"><hh:leftBorder type="SOLID" width="0.12 mm"/><hh:rightBorder type="SOLID" width="0.12 mm"/><hh:topBorder type="SOLID" width="0.12 mm"/><hh:bottomBorder type="SOLID" width="0.12 mm"/></hh:borderFill>
 <hh:borderFill id="3"><hh:leftBorder type="SOLID" width="0.12 mm"/><hh:rightBorder type="SOLID" width="0.12 mm"/><hh:topBorder type="SOLID" width="0.12 mm"/><hh:bottomBorder type="SOLID" width="0.12 mm"/><hc:fillBrush><hc:winBrush faceColor="#D6D6D6" hatchColor="#000000" alpha="0"/></hc:fillBrush></hh:borderFill>
</hh:borderFills>
<hh:charProperties itemCnt="2">
 <hh:charPr id="0" height="1000" textColor="#000000"><hh:fontRef hangul="0"/></hh:charPr>
 <hh:charPr id="1" height="1600" textColor="#2525F5"><hh:fontRef hangul="0"/><hh:bold/></hh:charPr>
</hh:charProperties>
<hh:paraProperties itemCnt="2">
 <hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/><hh:heading type="NONE" level="0"/></hh:paraPr>
 <hh:paraPr id="1"><hh:align horizontal="CENTER"/><hh:heading type="OUTLINE" level="0"/></hh:paraPr>
</hh:paraProperties>
<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" paraPrIDRef="0" charPrIDRef="0"/></hh:styles>
</hh:refList></hh:head>"""

SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
<hp:p paraPrIDRef="1" styleIDRef="0"><hp:run charPrIDRef="0"><hp:secPr><hp:pagePr landscape="WIDELY" width="59528" height="84188"><hp:margin left="5669" right="5669" top="4251" bottom="2834" header="2834" footer="2834" gutter="0"/></hp:pagePr></hp:secPr></hp:run>
 <hp:run charPrIDRef="1"><hp:t>사업 개요</hp:t></hp:run></hp:p>
<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0"><hp:t>첫 줄<hp:lineBreak/>둘째 줄</hp:t></hp:run></hp:p>
<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0"><hp:tbl rowCnt="2" colCnt="2" borderFillIDRef="2"><hp:sz width="40000"/>
 <hp:tr>
  <hp:tc borderFillIDRef="3"><hp:subList vertAlign="CENTER"><hp:p paraPrIDRef="0"><hp:run charPrIDRef="1"><hp:t>제목 칸</hp:t></hp:run></hp:p></hp:subList><hp:cellAddr rowAddr="0" colAddr="0"/><hp:cellSpan rowSpan="1" colSpan="2"/><hp:cellSz width="40000" height="1000"/></hp:tc>
 </hp:tr>
 <hp:tr>
  <hp:tc borderFillIDRef="2"><hp:subList vertAlign="TOP"><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:tbl rowCnt="1" colCnt="1" borderFillIDRef="2"><hp:sz width="10000"/><hp:tr><hp:tc borderFillIDRef="2"><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>안쪽 표</hp:t></hp:run></hp:p></hp:subList><hp:cellAddr rowAddr="0" colAddr="0"/><hp:cellSpan rowSpan="1" colSpan="1"/><hp:cellSz width="10000" height="500"/></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hp:subList><hp:cellAddr rowAddr="1" colAddr="0"/><hp:cellSpan rowSpan="1" colSpan="1"/><hp:cellSz width="10000" height="90000"/></hp:tc>
  <hp:tc borderFillIDRef="2"><hp:subList vertAlign="TOP"><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>오른쪽</hp:t></hp:run></hp:p></hp:subList><hp:cellAddr rowAddr="1" colAddr="1"/><hp:cellSpan rowSpan="1" colSpan="1"/><hp:cellSz width="30000" height="1000"/></hp:tc>
 </hp:tr>
</hp:tbl></hp:run></hp:p>
</hs:sec>"""


def _hwpx(tmp_path):
    p = tmp_path / "t.hwpx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/header.xml", HEADER)
        zf.writestr("Contents/section0.xml", SECTION)
    return p


def test_convert_keeps_styles_tables_and_page(tmp_path):
    data, stats = hwpx_docx.convert(_hwpx(tmp_path))
    assert stats["tables"] == 1 and stats["nested_tables"] == 1
    d = Document(io.BytesIO(data))
    # 개요 문단은 제목 스타일, 굵고 파란 16pt 글자
    h = d.paragraphs[0]
    assert h.style.name.startswith("Heading") and h.runs[0].bold and h.runs[0].font.size.pt == 16
    assert str(h.runs[0].font.color.rgb) == "2525F5"
    # 줄 바꿈은 같은 문단 안의 br
    assert len(d.paragraphs[1]._p.findall(".//" + qn("w:br"))) == 1
    # 표: 첫 행 병합 + 회색 바탕, 둘째 행 왼쪽 칸에 중첩 표, 오른쪽 칸 글
    t = d.tables[0]
    assert len(t.rows) == 2 and len(t.columns) == 2
    head = t.cell(0, 0)
    assert head.text == "제목 칸" and head._tc is t.cell(0, 1)._tc
    shd = head._tc.tcPr.find(qn("w:shd"))
    assert shd is not None and shd.get(qn("w:fill")) == "D6D6D6"
    assert t.cell(1, 0).tables and t.cell(1, 0).tables[0].cell(0, 0).text == "안쪽 표"
    assert t.cell(1, 1).text == "오른쪽"
    # 배치용 큰 행 높이는 상한(5인치)으로 — 빈 쪽을 만들지 않게. 중첩 표가 든 행은 높이를 정하지 않는다
    trh = t.rows[1]._tr.find(qn("w:trPr"))
    assert trh is None or trh.find(qn("w:trHeight")) is None
    # 쪽: A4 세로, 여백
    sec = d.sections[0]
    assert round(sec.page_width.inches, 2) == 8.27 and round(sec.page_height.inches, 2) == 11.69
    assert round(sec.left_margin.inches, 2) == 0.79


def test_encrypted_section_raises(tmp_path):
    p = tmp_path / "e.hwpx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Contents/header.xml", HEADER)
        zf.writestr("Contents/section0.xml", b"\x00\x01garbage")
    import pytest

    with pytest.raises(ValueError):
        hwpx_docx.convert(p)


def test_column_grid_merges_rows_with_different_boundaries():
    """행마다 열 경계가 다른 배치용 표(작성서식 4×13): 공통 그리드로 옮겨도 각 셀의 x 범위가 보존된다."""
    from xml.etree import ElementTree as ET

    def tc(w, col, rs=1, cs=1):
        el = ET.Element("tc")
        ET.SubElement(el, "cellAddr", rowAddr="0", colAddr=str(col)); ET.SubElement(el, "cellSpan", rowSpan=str(rs), colSpan=str(cs))
        ET.SubElement(el, "cellSz", width=str(w), height="500")
        return el
    # 0행: 6434 | 848×4 | 29094(3칸 병합) | 848×4 | 5685 ; 1행: 20953(6칸) | 17684 | 9077(6칸)
    r0 = [(0, 0, 1, 1, tc(6434, 0))] + [(0, c, 1, 1, tc(848, c)) for c in (1, 2, 3, 4)] + [(0, 5, 1, 3, tc(29094, 5, 1, 3))] \
         + [(0, c, 1, 1, tc(848, c)) for c in (8, 9, 10, 11)] + [(0, 12, 1, 1, tc(5685, 12))]
    r1 = [(1, 0, 1, 6, tc(20953, 0, 1, 6)), (1, 6, 1, 1, tc(17684, 6)), (1, 7, 1, 6, tc(9077, 7, 1, 6))]
    col_hu, cells = hwpx_docx._column_grid([None, None], r0 + r1)
    assert sum(col_hu) == 47997 or abs(sum(col_hu) - 47997) <= 2 * hwpx_docx.GRID_TOL
    # 1행 가운데 셀(17684)은 x 20953~38637 — 그리드 열 합이 그 폭이어야 한다
    mid = next(c for c in cells if c[0] == 1 and c[1] > 0 and c[3] < 6)
    start, span = mid[1], mid[3]
    assert abs(sum(col_hu[start:start + span]) - 17684) <= 2 * hwpx_docx.GRID_TOL
    assert len(col_hu) >= 13


def test_empty_paragraphs_before_page_break_are_dropped(tmp_path):
    section = SECTION.replace('<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0"><hp:t>첫 줄<hp:lineBreak/>둘째 줄</hp:t></hp:run></hp:p>',
                              '<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0"><hp:t>첫 줄<hp:lineBreak/>둘째 줄</hp:t></hp:run></hp:p>'
                              '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"/></hp:p><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"/></hp:p>'
                              '<hp:p paraPrIDRef="0" pageBreak="1"><hp:run charPrIDRef="0"><hp:t>다음 쪽</hp:t></hp:run></hp:p>')
    p = tmp_path / "b.hwpx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Contents/header.xml", HEADER); zf.writestr("Contents/section0.xml", section)
    data, _ = hwpx_docx.convert(p)
    d = Document(io.BytesIO(data))
    texts = [x.text for x in d.paragraphs]
    i = texts.index("다음 쪽")
    assert texts[i - 1] != ""                                  # 쪽 나눔 앞 빈 문단이 사라졌다
    assert d.paragraphs[i].paragraph_format.page_break_before


def test_hanging_indent_and_exact_line_spacing(tmp_path):
    header = HEADER.replace('<hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/><hh:heading type="NONE" level="0"/></hh:paraPr>',
                            '<hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/><hh:heading type="NONE" level="0"/>'
                            '<hh:margin><hc:intent value="-1000" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/></hh:margin>'
                            '<hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/></hh:paraPr>')
    p = tmp_path / "i.hwpx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Contents/header.xml", header); zf.writestr("Contents/section0.xml", SECTION)
    data, _ = hwpx_docx.convert(p)
    d = Document(io.BytesIO(data))
    pf = d.paragraphs[1].paragraph_format                     # '첫 줄/둘째 줄' 문단(10pt)
    assert round(pf.left_indent.inches, 3) == round(1000 / 7200, 3) and round(pf.first_line_indent.inches, 3) == -round(1000 / 7200, 3)
    assert pf.line_spacing.pt == 16.0


def test_normalize_image_reencodes_icc_jpeg_and_bmp():
    import io as _io

    from PIL import Image

    buf = _io.BytesIO(); Image.new("RGB", (4, 4), "white").save(buf, format="JPEG", icc_profile=b"\x00" * 128)
    raw = buf.getvalue()
    jpg = b"\xff\xd8" + raw[raw.index(b"\xff\xe2"):]           # JFIF 머리를 떼어 ICC 프로필(APP2)로 시작하게 — 워드가 거절하는 머리
    assert jpg[:4] == b"\xff\xd8\xff\xe2"
    out = hwpx_docx.normalize_image(jpg)
    assert out[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1")
    buf = _io.BytesIO(); Image.new("RGB", (4, 4), "white").save(buf, format="BMP")
    out = hwpx_docx.normalize_image(buf.getvalue())
    assert out[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\x89PNG")   # 불투명 BMP 는 JPEG, 투명이면 PNG — 워드가 받는 형식이면 된다
    assert hwpx_docx.normalize_image(b"not an image") == b"not an image"


def test_normalize_image_downscales_large_pictures():
    import io as _io

    from PIL import Image

    buf = _io.BytesIO(); Image.new("RGB", (4000, 3000), "white").save(buf, format="PNG")
    out = hwpx_docx.normalize_image(buf.getvalue())
    im = Image.open(_io.BytesIO(out))
    assert max(im.size) == 1600 and im.format == "JPEG"
    buf = _io.BytesIO(); Image.new("RGBA", (3200, 100), (0, 0, 0, 0)).save(buf, format="PNG")
    im2 = Image.open(_io.BytesIO(hwpx_docx.normalize_image(buf.getvalue())))
    assert im2.format == "PNG" and max(im2.size) == 1600                # 투명 그림은 PNG 유지
    small = _io.BytesIO(); Image.new("RGB", (100, 100), "white").save(small, format="PNG")
    assert hwpx_docx.normalize_image(small.getvalue()) == small.getvalue()
