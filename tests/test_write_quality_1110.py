"""쓰기 품질(11-10 대화 190) — 서식 표는 새로 만들지 않고 채운 꼴로, 기관 정보로 아는 값, 절 제목 되풀이, 한 문서 안 번호 기호."""
from __future__ import annotations

import io
import re
import zipfile

from tests.test_webhwp_fields import _hwpx, _p, _table
from zzaimy.app import webhwp_fields as wf
from zzaimy.app import webhwp_write as ww


def _xml(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()


def _form():
    sec = (_p("1. 참여학과 현황", 11)
           + _table([["단과대학", "참여학과명", "학과 정원", "교원 수"], ["자연과학대학", "", "", ""], ["합계", "", "", ""]], 100)
           + _p("2. 추진 계획", 21))
    data, _ = wf.fieldize(_hwpx(sec))
    return data


def test_body_table_with_same_head_replaces_form_table_not_doubled():
    """본문이 서식 표와 같은 머리줄의 표를 들고 오면 서식 표 자리를 대신한다 — 견본 라벨(자연과학대학)이 든 서식 표가 아래 따로 남지 않는다."""
    data = _form()
    assert ww.section_form_heads(data, "절:1. 참여학과 현황")[0][:4] == ["단과대학", "참여학과명", "학과 정원", "교원 수"]
    body = "1) 참여학과 현황은 다음과 같음\n| 단과대학 | 참여학과명 | 학과 정원 | 교원 수 |\n|---|---|---|---|\n| 공학대학 | 기계과 | 40 | 6 |"
    out, put = ww.write_section(data, "절:1. 참여학과 현황", ww.parse_body(body))
    x = _xml(out)
    head = x.split("2. 추진 계획")[0]
    assert head.count("<hp:tbl") == 1 and "기계과" in head and "자연과학대학" not in head


def test_body_table_with_other_head_keeps_form_table():
    data = _form()
    body = "1) 일정\n| 월 | 내용 |\n|---|---|\n| 3월 | 착수 |"
    out, _ = ww.write_section(data, "절:1. 참여학과 현황", ww.parse_body(body))
    assert "자연과학대학" in _xml(out)                          # 다른 표 — 서식 표는 그대로


def test_heading_echo_with_different_number_is_dropped():
    assert ww.drop_heading_echo("1. 정원 감축 계획(총괄표)\n1) 본문", "절:1.1. 정원 감축 계획(총괄표)") == "1) 본문"
    assert ww.drop_heading_echo("## 모수 산정 총괄표\n가) 내용", "절:1.1.1. 모수 산정 총괄표") == "가) 내용"
    assert ww.drop_heading_echo("1) 정원 감축 계획은 다음과 같음", "절:1.1. 정원 감축 계획") == "1) 정원 감축 계획은 다음과 같음"


def test_markers_follow_the_document_pattern():
    """첫 절이 1) → 가) → (1) 로 썼으면 다음 절의 1. → 가. → (1) 도 같은 꼴로 — 순번은 그대로, 표·날짜 줄은 손대지 않는다."""
    first = "1) 개요\n가) 목적\n(1) 세부"
    pat = ww.marker_pattern(first)
    assert pat == [2, 3, 4]
    second = "1. 추진\n가. 내용\n나. 일정\n2. 성과\n| 1. 칸 | 2 |\n2026. 3. ~ 2027. 2."
    got, _ = ww.unify_markers(second, pat)
    assert got.split("\n")[:4] == ["1) 추진", "가) 내용", "나) 일정", "2) 성과"]
    assert "| 1. 칸 | 2 |" in got and "2026. 3. ~ 2027. 2." in got
    same, p2 = ww.unify_markers(first, None)
    assert same == first and p2 == [2, 3, 4]


def test_agent_write_unifies_numbering_and_brief_has_institution(tmp_path, monkeypatch):
    """작업본 쓰기(메인·서브 같은 길)가 첫 절의 번호 꼴을 기록하고 다음 절을 맞춘다. 서브 지시서에 번호 꼴·기관 정보·서식 표 안내가 들어간다."""
    from tests.test_delegate import _work
    from zzaimy.app import delegate, institution
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=211)
    institution.set_fact(db, "대학명", "가나다대학교")
    assert hw.write({"field": secs[0], "text": "1) 첫 항목\n가) 세부"}).startswith("「")
    assert hw.numbering_text() == "1) → 가)"
    assert hw.write({"field": secs[1], "text": "1. 둘째\n가. 세부"}).startswith("「")
    vals = {x["name"]: x["text"] for x in hw._fields()}
    assert vals[secs[1]].startswith("1) 둘째") and "가) 세부" in vals[secs[1]]
    b = delegate.brief(db, hw.sid, hw, secs[0], "요지", "", "", "써 줘", 32000)
    text = delegate.render_brief(b, 32000)
    assert "가나다대학교" in text and "1) → 가)" in text


def test_sample_cells_and_guide_box_beside_table_are_cleaned():
    """첨부 서식 정리 — 견본 칸(OO학과·00명·…)은 비우고, 내용 표 옆에 붙은 【작성방법】 상자는 빼서 안내로 남긴다. 견본 기관 이름은 기관 정보로."""
    for t in ("OO학과(OO전공)", "00명", "000", "(0000000)", "ㅇㅇ학부(과)", "① ㅇㅇㅇㅇㅇ", "…", "00개 학과"):
        assert wf.sample_cell(t), t
    for t in ("합계", "자연과학대학", "2026. 8.", "학과 정원", "○○대학교 총장"):
        assert not wf.sample_cell(t), t
    box = _table([["【작성방법】 1) 참여학과를 작성"]], 300).replace('<hp:tbl id="301">', '<hp:tbl id="301" rowCnt="1" colCnt="1">')
    content = _table([["학과", "정원"], ["OO학과", "00명"]], 200)
    both = content.replace("</hp:tbl></hp:run></hp:p>", "</hp:tbl>" + re.search(r"<hp:tbl\b.*</hp:tbl>", box).group(0) + "</hp:run></hp:p>")
    sec = _p("1. 참여 현황", 11) + both + _p("○○대학교 총장", 500)
    data, _n = wf.clear_samples(_hwpx(sec), {"대학명": "가나다대학교"})
    data, fields = wf.fieldize(data)
    data, guides, removed = wf.submission_clean(data)
    x = _xml(data)
    assert "작성방법" not in x and "OO학과" not in x and "00명" not in x and "가나다대학교 총장" in x
    assert any("참여학과를 작성" in v for v in guides.values())
