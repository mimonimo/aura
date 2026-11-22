"""작년 완성본에서 시작한 작업본 — 남은 글을 번호로 보이고(leftovers_text), 27B 가 고른 번호만 비운다(clear_items, 11-23).
요청 낱말(「틀」「뼈대」)로 나누지 않고, 무엇이 짜임이고 무엇이 지난 내용인지는 에이전트가 판단한다 — 코드는 목록과 비우기만."""
from __future__ import annotations

import io
import re
import zipfile

from zzaimy.app import webhwp_edit_ops as eo


def _hwpx(section: str) -> bytes:
    from hwpx import HwpxDocument
    base = HwpxDocument.new().to_bytes()
    zin = zipfile.ZipFile(io.BytesIO(base))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                body = re.sub(r"(<hs:sec\b[^>]*>)", r"\1" + section, body.decode(), count=1).encode()
            z.writestr(info, body)
    return buf.getvalue()


def _p(text: str, pid: int) -> str:
    return f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'


def _table(rows: list[list[str]], base: int) -> str:
    trs = "".join("<hp:tr>" + "".join(
        f'<hp:tc><hp:cellAddr colAddr="{j}" rowAddr="{i}"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList>{_p(c, base + i * 10 + j)}</hp:subList></hp:tc>'
        for j, c in enumerate(r)) + "</hp:tr>" for i, r in enumerate(rows))
    return (f'<hp:p id="{base}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
            f'<hp:tbl id="{base + 1}" rowCnt="{len(rows)}" colCnt="{len(rows[0])}">{trs}</hp:tbl></hp:run></hp:p>')


def _texts(data: bytes) -> str:
    return " ".join(re.findall(r"<hp:t>([^<]*)</hp:t>", zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()))


DOC = (_p("「2025 가상 사업」 결과보고서", 1) + _p("1. 사업 개요", 2)
       + _table([["사업명", "2025 가상 사업"], ["주관 부서", "가상 산학협력단"]], 100)
       + _p("2025년에는 특강과 캠프를 운영하였다.", 3))


def test_leftovers_lists_cells_and_paragraphs_with_ids():
    lt = eo.leftovers_text(_hwpx(DOC))
    assert "표1:1,2 「2025 가상 사업」" in lt and "표1:1,1 「사업명」" in lt
    assert "글1 「「2025 가상 사업」 결과보고서」" in lt and "글2 「1. 사업 개요」" in lt and "글3 「2025년에는" in lt


def test_clear_items_blanks_only_chosen_and_keeps_structure():
    data = _hwpx(DOC)
    out, msg = eo.clear_items(data, {"ids": ["표1:1,2", "글3", "글1", "표9:1,1"]})
    t = _texts(out)
    assert "2025 가상 사업" not in t and "2025년에는" not in t
    assert "사업명" in t and "1. 사업 개요" in t and "가상 산학협력단" in t
    assert "3곳을 비웠다" in msg and "표9:1,1" in msg
    lt = eo.leftovers_text(out)
    assert "표1:1,2" not in lt and "표1:2,2 「가상 산학협력단」" in lt
