"""기안기 작업본 서버 쪽 쓰기 — 칸·표 필드 값, 절 본문(문단·표) 넣기·다시 쓰기, 마크다운 표 읽기."""
from __future__ import annotations

import io
import re
import zipfile

from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


def _xml(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()


def test_fill_fields_and_write_sections():
    form = wt.build(wt.GRANT["budget_change"])
    names = {f["name"]: f for f in ww.fields(form)}
    assert names["칸:사업명"]["kind"] == "칸" and names["절:1. 변경 사유"]["kind"] == "절"
    data, done = ww.set_fields(form, {"칸:사업명": "AI 인재 양성", "칸:담당자": "홍길동\n(내선 1234)", "없는 칸": "x"})
    assert done == ["칸:사업명", "칸:담당자"]
    f2 = {f["name"]: f["text"] for f in ww.fields(data)}
    assert f2["칸:사업명"] == "AI 인재 양성" and "홍길동" in f2["칸:담당자"]
    body = "1) 2차년도 집행 계획 조정\n가) 장비 단가 상승 반영\n| 비목 | 당초 | 변경 |\n|---|---|---|\n| 기자재 | 100 | 120 |"
    items = ww.parse_body(body)
    assert [k for k, _ in items] == ["text", "table"] and items[1][1][1] == ["기자재", "100", "120"]
    data2, put = ww.write_section(data, "절:1. 변경 사유", items)
    x = _xml(data2)
    assert "장비 단가 상승 반영" in x and "기자재" in x and len(put) == 2
    assert {f["name"]: f["text"] for f in ww.fields(data2)}["절:1. 변경 사유"] == "1) 2차년도 집행 계획 조정"   # 첫 줄은 필드 안
    assert x.index("1. 변경 사유") < x.index("장비 단가 상승 반영") < x.index("2. 변경 내역")   # 그 절 자리에
    # 다시 쓰기 — 앞서 넣은 것은 지우고 새로
    data3, put2 = ww.write_section(data2, "절:1. 변경 사유", ww.parse_body("1) 새 사유"), {"절:1. 변경 사유": put})
    x3 = _xml(data3)
    assert "새 사유" in x3 and "장비 단가 상승 반영" not in x3 and "2차년도 집행 계획 조정" not in x3 and "기자재" not in x3.split("2. 변경 내역")[0]
    assert x3.count("<hp:tbl") == _xml(data).count("<hp:tbl")


def test_write_section_inside_table_cell():
    """테두리 표 안에 절 제목이 있는 양식 — 칸 폭에 맞춰 표를 넣는다."""
    from tests.test_webhwp_fields import _hwpx, _table
    from zzaimy.app import webhwp_fields as wf
    data, _ = wf.fieldize(_hwpx(_table([["1. 기술지도 내역"], [""]], 100)))
    out, put = ww.write_section(data, "절:1. 기술지도 내역", [("text", "1) 내역"), ("table", [["회차", "내용"], ["1", "진단"]])])
    x = _xml(out)
    assert "진단" in x and x.count("<hp:tbl") == 2 and put


def test_house_table_style_and_restyle():
    import io, re, zipfile
    from zzaimy.app import webhwp_style as st, webhwp_templates as t
    data = t.build(t.GRANT["budget_change"])
    tl = st.tables(data)
    assert tl and tl[1]["head"].startswith("비목")
    hdr = zipfile.ZipFile(io.BytesIO(data)).read("Contents/header.xml").decode()
    assert 'faceColor="#E5E5E5"' in hdr and 'width="0.4 mm"' in hdr and "<hh:bold/>" in hdr
    again = st.apply(data, [2], {"head_fill": "연파랑", "head_bottom": "DOUBLE", "font_pt": 11})
    h2 = zipfile.ZipFile(io.BytesIO(again)).read("Contents/header.xml").decode()
    assert 'faceColor="#DFEAF5"' in h2 and "DOUBLE_SLIM" in h2 and 'height="1100"' in h2
    tbl = st._TBL.findall(zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode())
    r = st.roles(tbl[0])
    assert r[(0, 0)] == "label" and r[(0, 1)] == "body"
    assert set(st.roles(tbl[1])[(0, c)] for c in range(6)) == {"head"}
