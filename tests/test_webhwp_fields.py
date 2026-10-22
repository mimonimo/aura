"""한글 양식 필드 박기 — 절 제목(표 칸 안 포함)·라벨 칸·목록표(예시 행)에 이름 붙은 누름틀을 넣고, 다시 박지 않는다."""
from __future__ import annotations

import io
import re
import zipfile

from zzaimy.app import webhwp_fields as wf


def _hwpx(section: str) -> bytes:
    from hwpx import HwpxDocument
    base = HwpxDocument.new().to_bytes()
    zin = zipfile.ZipFile(io.BytesIO(base))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                x = body.decode()
                body = re.sub(r"(<hs:sec\b[^>]*>)", r"\1" + section, x, count=1).encode()
            z.writestr(info, body)
    return buf.getvalue()


def _p(text: str, pid: int) -> str:
    return f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'


def _cell(text: str, pid: int) -> str:
    inner = _p(text, pid) if text else f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"/></hp:p>'
    return f'<hp:tc><hp:subList>{inner}</hp:subList></hp:tc>'


def _table(rows: list[list[str]], base: int) -> str:
    trs = "".join("<hp:tr>" + "".join(_cell(c, base + i * 10 + j) for j, c in enumerate(r)) + "</hp:tr>" for i, r in enumerate(rows))
    return f'<hp:p id="{base}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:tbl id="{base + 1}">{trs}</hp:tbl></hp:run></hp:p>'


def _names(data: bytes) -> list[str]:
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    return re.findall(r'<hp:fieldBegin\b[^>]*name="([^"]*)"', x)


def test_fields_for_headings_labels_and_list_tables():
    sec = (_p("1. 사업 개요", 11)
           + _table([["기관명", "", "대표자", ""], ["2. 추진 계획", "", "", ""]], 100)
           + _table([["소속", "직위", "이름"], ["ㅇㅇ대학교", "총장", "ㅇㅇㅇ"]], 300))
    data, fields = wf.fieldize(_hwpx(sec))
    names = _names(data)
    assert "절:1. 사업 개요" in names and "절:2. 추진 계획" in names          # 표 칸 안의 절 제목도
    assert "칸:기관명" in names and "칸:대표자" in names
    assert {"표2:소속", "표2:직위", "표2:이름"} <= set(names)                  # 예시 행을 필드로 감쌈
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    i = x.find('name="표2:이름"')
    assert "ㅇㅇㅇ" in x[i:i + 900]                                           # 예시 글자는 필드 안에 남는다(쓰면 바뀜)
    assert len(fields) == len(names)
    again, f2 = wf.fieldize(data)                                              # 이미 박은 양식은 그대로
    assert again == data and all(f["kind"] == "기존" for f in f2)


def test_common_templates_get_fields():
    from zzaimy.app import webhwp_templates as wt
    from zzaimy.ingest.gdocs_templates import SPECS
    names = _names(wt.build(SPECS["minutes"]))          # 만들 때 이미 필드를 박는다
    assert any(n.startswith("절:") for n in names) and len(names) >= 5


def test_zip_bomb_is_refused():
    import pytest
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", "<a>" + "0" * (20 * 1024 * 1024) + "</a>")   # 20MB 가 수십 KB 로 — 압축률 수백 배
    with pytest.raises(ValueError):
        wf.fieldize(buf.getvalue())
    with pytest.raises(ValueError):
        wf.fieldize(b"not a zip")


def test_guide_boxes_toc_and_notes_are_not_slots():
    sec = (_p("1. 사업 개요 3", 11)                                            # 목차 줄(끝에 쪽 번호)
           + _p("2) 지표값 산정 시 소수점 첫째자리까지 기재", 12)                 # 안내 문장
           + _table([["【작성방법】"], ["1. 추진 배경을 쓴다"]], 100)              # 작성 안내 상자
           + _p("1. 추진 배경", 13) + _p("", 14)                                # 진짜 제목 + 빈 자리
           + _p("2. 추진 내용", 15) + _p("이 사업은 지역과 대학이 함께 인재를 기르는 사업으로 다음 사항을 빠짐없이 작성하여 제출하여야 합니다.", 16)
           + _table([["98.0%", ""], ["ㅇㅇ학부(과)", ""], ["사업계획서 목차", ""], ["담당자", ""]], 300))
    names = _names(wf.fieldize(_hwpx(sec))[0])
    assert "절:1. 추진 배경" in names
    assert not any(n.startswith(("절:1. 사업 개요", "절:2) 지표값", "절:1. 추진 배경을")) for n in names)
    assert "절:2. 추진 내용" not in names                                       # 뒤에 긴 안내 글 — 쓸 자리가 아니다
    assert "칸:담당자" in names
    assert not any(n in names for n in ("칸:98.0%", "칸:ㅇㅇ학부(과)", "칸:사업계획서 목차"))


def test_body_slot_goes_below_guide_box():
    sec = _p("1. 추진 배경", 11) + _table([["【작성방법】 배경을 쓴다"]], 100) + _p("2. 추진 내용", 15) + _p("", 16)
    data, _ = wf.fieldize(_hwpx(sec))
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    assert x.find("【작성방법】") < x.find('name="절:1. 추진 배경"') < x.find("2. 추진 내용")


def test_heading_followed_by_plain_line_keeps_slot():
    # 기안문 「2. 추진 내용」 뒤 「붙임 … 끝.」 — 작성 지시가 아닌 긴 줄이 와도 쓸 자리다
    sec = _p("2. 추진 내용", 11) + _p("붙임  1. (붙임 문서 이름) 1부.  끝.", 12) + _p("", 13)
    assert "절:2. 추진 내용" in _names(wf.fieldize(_hwpx(sec))[0])


def test_lineseg_kept_outside_edited_paragraphs():
    seg = '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="42520" flags="393216"/></hp:linesegarray>'
    keep = f'<hp:p id="20" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>그대로 둘 문단</hp:t></hp:run>{seg}</hp:p>'
    sec = _p("1. 추진 배경", 11) + _p("", 12) + keep
    data, _ = wf.fieldize(_hwpx(sec))
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    i = x.find("그대로 둘 문단")
    assert "<hp:linesegarray>" in x[i:i + 600]                    # 글을 바꾸지 않은 문단의 배치는 남긴다
