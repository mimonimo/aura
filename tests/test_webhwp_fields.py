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
