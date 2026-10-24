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


def test_diagram_block_becomes_box_table():
    import io, re, zipfile
    from zzaimy.app import webhwp_templates as t, webhwp_write as ww
    data = t.build(t.GRANT["budget_change"])
    name = next(f["name"] for f in ww.fields(data) if f["kind"] == "절")
    body = "1) 추진 체계\n```도식\n총장\n사업단 → 운영위원회 → 성과관리센터\n```\n2) 운영 방식"
    items = ww.parse_body(body)
    assert [k for k, _ in items] == ["text", "diagram", "text"]
    out, put = ww.write_section(data, name, items, {})
    x = zipfile.ZipFile(io.BytesIO(out)).read("Contents/section0.xml").decode()
    assert "⟦도식" not in x and "운영위원회" in x and "↓" in x and "→" in x
    assert re.search(r'colSpan="5"', x)                          # 상자 하나뿐인 줄은 가로로 꽉
    again, _ = ww.write_section(out, name, ww.parse_body("1) 새 본문"), {name: put})
    y = zipfile.ZipFile(io.BytesIO(again)).read("Contents/section0.xml").decode()
    assert "운영위원회" not in y                                   # 다시 쓰면 도식도 지워진다


def _p_ids(data: bytes) -> list[str]:
    import re as _re
    return _re.findall(r'<hp:p\b[^>]*\bid="(\d+)"', _xml(data))


def test_multiline_values_become_paragraphs_not_line_breaks():
    """여러 줄 값은 진짜 문단 나눔으로 — 기안기 GetFieldText 는 줄 나눔(lineBreak)을 돌려주지 않아 공동 편집 한 번에 줄이 붙었다(ADR-0070)."""
    form = wt.build(wt.GRANT["budget_change"])
    data, done = ww.set_fields(form, {"절:1. 변경 사유": "첫 줄\n둘째 줄\n셋째 줄", "칸:담당자": "홍길동\n(내선 1234)"})
    assert sorted(done) == ["절:1. 변경 사유", "칸:담당자"]
    x = _xml(data)
    assert "lineBreak" not in x
    ids = _p_ids(data)
    assert len(ids) == len(set(ids)) and len(ids) == len(_p_ids(form)) + 3       # 새 문단 셋(절 둘·칸 하나), id 겹침 없음
    vals = ww.field_values(data)
    assert vals["절:1. 변경 사유"] == "첫 줄\n둘째 줄\n셋째 줄" and vals["칸:담당자"] == "홍길동\n(내선 1234)"
    import xml.dom.minidom
    xml.dom.minidom.parseString(x.encode())                                        # 잘 짜인 XML
    # 여러 문단에 걸친 필드를 다시 한 줄로 — 걸쳤던 문단이 하나로 돌아온다
    data2, _ = ww.set_fields(data, {"절:1. 변경 사유": "한 줄"})
    assert ww.field_values(data2)["절:1. 변경 사유"] == "한 줄" and len(_p_ids(data2)) == len(_p_ids(form)) + 1
    xml.dom.minidom.parseString(_xml(data2).encode())
    # 50,000자 상한 없이 그대로 들어간다
    long = "\n".join("가" * 990 for _ in range(60))
    assert ww.field_values(ww.set_fields(form, {"절:1. 변경 사유": long})[0])["절:1. 변경 사유"] == long


def test_normalize_breaks_turns_old_line_breaks_into_paragraphs():
    """예전 판(줄 나눔으로 쓴 필드)은 한 번 문단 나눔으로 바꾼다 — 값은 그대로, 문단만 나뉜다. 두 번째는 바꿀 것이 없다."""
    import re as _re
    form = wt.build(wt.GRANT["budget_change"])
    zin = zipfile.ZipFile(io.BytesIO(ww.set_fields(form, {"절:1. 변경 사유": "가"})[0]))
    # 예전 꼴: 한 문단 안 줄 나눔
    old_x = zin.read("Contents/section0.xml").decode().replace("<hp:t>가</hp:t>", "<hp:t>가<hp:lineBreak/>나<hp:lineBreak/>다</hp:t>", 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in zin.infolist():
            z.writestr(i, old_x.encode() if i.filename == "Contents/section0.xml" else zin.read(i.filename))
    old = buf.getvalue()
    assert ww.field_values(old)["절:1. 변경 사유"] == "가\n나\n다"
    new, n = ww.normalize_breaks(old)
    assert n == 2 and "lineBreak" not in _xml(new) and ww.field_values(new) == ww.field_values(old)
    ids = _re.findall(r'<hp:p\b[^>]*\bid="(\d+)"', _xml(new))
    assert len(ids) == len(set(ids))
    assert ww.normalize_breaks(new) == (new, 0)
