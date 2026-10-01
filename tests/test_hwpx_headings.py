"""hwpx 절 제목 판정 — 개요 문단, 또는 짧고 번호로 시작하며 본문보다 크거나 굵은 글자(ADR-0048). 실물 서식은 개요 스타일을 거의 안 쓴다."""

import zipfile

from zzaimy.ingest.parsers.hwpx import HwpxParser, is_heading

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"><hh:refList>
<hh:charProperties itemCnt="3">
 <hh:charPr id="0" height="1000"/>
 <hh:charPr id="1" height="1300"/>
 <hh:charPr id="2" height="1000"><hh:bold/></hh:charPr>
</hh:charProperties>
<hh:paraProperties itemCnt="2">
 <hh:paraPr id="0"><hh:heading type="NONE" level="0"/></hh:paraPr>
 <hh:paraPr id="1"><hh:heading type="OUTLINE" level="0"/></hh:paraPr>
</hh:paraProperties>
<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" paraPrIDRef="0" charPrIDRef="0"/></hh:styles>
</hh:refList></hh:head>"""


def _p(text, cp="0", pp="0"):
    return f'<hp:p paraPrIDRef="{pp}" styleIDRef="0"><hp:run charPrIDRef="{cp}"><hp:t>{text}</hp:t></hp:run></hp:p>'


def test_numbered_large_or_bold_short_paragraphs_become_headings(tmp_path):
    body = "".join([
        _p("1. 사업 개요", "1"),                                   # 크다 → 제목
        _p("1.1. 추진 배경", "2"),                                 # 굵다 → 제목
        _p("Ⅱ. 추진 계획", "0", "1"),                              # 개요 문단 → 제목
        _p("□ 지역 산업 동향", "1"),                               # 개조식 부호 → 본문
        _p("1. " + "긴 문장 " * 15, "1"),                          # 60자 넘음 → 본문
        _p("가. 본문 크기의 번호 항목", "0"),                       # 본문 크기·안 굵음 → 본문
        _p("지역 산업 수요가 늘고 있다.", "0"),
    ])
    sec = f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">{body}</hs:sec>'
    path = tmp_path / "f.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/header.xml", HEADER)
        zf.writestr("Contents/section0.xml", sec)
    res = HwpxParser().parse(path, work_dir=tmp_path / "img")
    heads = [e.text for e in res.entries if e.kind == "heading"]
    assert heads == ["1. 사업 개요", "1.1. 추진 배경", "Ⅱ. 추진 계획"]


def test_is_heading_rules():
    assert is_heading("2.1.3. 성과지표", (1000, True), False, 1000)
    assert not is_heading("○ 성과지표", (1400, True), False, 1000)
    assert not is_heading("2025년 실적은 다음과 같다", (1400, True), False, 1000)


def test_dates_and_toc_lines_are_not_headings():
    from zzaimy.ingest.parsers.base import ParsedEntry
    from zzaimy.ingest.parsers.hwpx import demote_toc_lines

    assert not is_heading("2026. 2.", (1400, True), False, 1000)
    assert not is_heading("2023. 6. 30.", (1400, True), False, 1000)
    entries = [ParsedEntry(page_no=1, kind="heading", text="1. 사업 비전 및 목표1"),
               ParsedEntry(page_no=1, kind="heading", text="2. 산학연협력 체제17"),
               ParsedEntry(page_no=2, kind="heading", text="1. 사업 비전 및 목표"),
               ParsedEntry(page_no=3, kind="heading", text="2. 산학연협력 체제"),
               ParsedEntry(page_no=3, kind="heading", text="3. 2025년 목표 3")]
    assert demote_toc_lines(entries) == 2
    assert [e.kind for e in entries] == ["text", "text", "heading", "heading", "heading"]
