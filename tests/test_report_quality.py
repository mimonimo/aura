"""결과보고서(작년 양식 살려 쓰기) 품질 — 「<br>」 글자, 다 쓴 절의 작성방법 상자, 말줄임 자리표시 행, 절 사이 되풀이(11-10 실물 결함)."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from zzaimy.app import webhwp_agent as wa
from zzaimy.app import webhwp_fields as wf
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww
from zzaimy.ingest import hwpx_fill as hf


def _xml(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()


def _form() -> bytes:
    """위 단계 제목(제 필드 없음)과 아래 두 절, 절마다 작성방법 상자 — 작년 양식 꼴."""
    return wt.build({"id": "t-rq", "title": "시험 보고서", "cover": {"title": "시험", "page": False}, "page_number": False,
                     "blocks": [{"h": 1, "text": "1. 행사 운영"}, {"guide": "아래 절을 묶는 안내"},
                                {"h": 2, "text": "가. 전시 부문"}, {"guide": "구분·주요 내용 열의 표로 정리한다."}, {"p": ""},
                                {"h": 2, "text": "나. 프로그램 부문"}, {"guide": "프로그램별로 쓴다."}, {"p": ""}]})


# ---- 1. <br> ----

def test_br_in_table_cell_and_text_becomes_line_break():
    items = ww.parse_body("1) 구성<br>전시와 체험\n| 구분 | 주요 내용 |\n|---|---|\n| 전시 | ● 경진대회<br>● 특강 <br/> ● 실습 |")
    assert items[0] == ("text", "1) 구성\n전시와 체험")
    assert items[1][1][1] == ["전시", "● 경진대회\n● 특강 \n ● 실습"]          # 표 줄은 끊기지 않고 칸 안 줄바꿈으로
    assert wf.clean_text("가<BR />나") == "가\n나" and wf.clean_text("가<br>나", breaks=False) == "가<br>나"
    assert hf.split_lines("a<br>b<br/>c") == ["a", "b", "c"]
    form = _form()
    out, _ = ww.write_section(form, "절:가. 전시 부문", items)
    x = _xml(out)
    assert "&lt;br" not in x and "<br" not in x and "경진대회" in x and "특강" in x


# ---- 2. 작성방법 상자 ----

def test_written_section_guide_box_removed_unwritten_kept():
    form = _form()
    assert _xml(form).count("【작성방법】") == 3
    out, _ = ww.write_section(form, "절:가. 전시 부문", ww.parse_body("1) 전시 운영함"))
    filled = [k for k, v in ww.field_values(out).items() if v.strip()]
    out2, got = ww.drop_guides(out, filled)
    x = _xml(out2)
    assert set(got) == {"절:가. 전시 부문"} and "구분·주요 내용" in got["절:가. 전시 부문"]
    assert "구분·주요 내용 열의 표로" not in x and "프로그램별로 쓴다" in x and "아래 절을 묶는 안내" in x   # 안 쓴 절·위 제목 안내는 남는다
    assert ww.field_values(out2) == ww.field_values(out)
    # 아래 절을 다 쓰면 위 단계 제목의 안내도 뗀다
    out3, _ = ww.write_section(out2, "절:나. 프로그램 부문", ww.parse_body("1) 특강 운영함"))
    out4, got4 = ww.drop_guides(out3, [k for k, v in ww.field_values(out3).items() if v.strip()])
    assert "【작성방법】" not in _xml(out4) and any(k.startswith("위:") for k in got4)


def test_kept_guide_is_still_read_after_removal(tmp_path):
    """뗀 안내는 작업본 기록에 남아 고쳐 쓰기·맡기기·요구 요소 판단이 계속 읽는다."""
    class _DB:
        def __init__(self):
            self.s = {}

        def get_setting(self, k, d=None):
            return self.s.get(k, d)

        def set_setting(self, k, v):
            self.s[k] = v
    db = _DB()
    p = tmp_path / "w.hwpx"
    form = _form()
    out, _ = ww.write_section(form, "절:가. 전시 부문", ww.parse_body("1) 전시 운영함"))
    out2, got = ww.drop_guides(out, ["절:가. 전시 부문"])
    p.write_bytes(out2)
    hw = wa.HwpTools.__new__(wa.HwpTools)
    hw.db = db
    hw.fid = lambda: 7
    from zzaimy.app import webhwp
    orig_meta, orig_get = webhwp.meta, webhwp.get
    webhwp.meta = lambda db_, fid: json.loads(db_.get_setting(f"webhwp_meta:{fid}", "{}") or "{}")
    webhwp.get = lambda db_, fid: {"id": fid, "path": str(p)}
    try:
        assert "구분·주요 내용" not in ww.section_context(out2, "절:가. 전시 부문")   # 문서에서는 뗐다
        hw._keep_guides(7, got)
        ctx = hw.section_ctx("절:가. 전시 부문")
        assert "구분·주요 내용" in wa.guide_of(ctx) and "표" in wa.needs_of(wa.guide_of(ctx))
        assert "표" in hw.needs_map().get("절:가. 전시 부문", [])
    finally:
        webhwp.meta, webhwp.get = orig_meta, orig_get


# ---- 3. 말줄임 자리표시 행 ----

def test_ellipsis_placeholder_rows_dropped():
    rows = [["NO", "주요프로그램", "위치"], ["1", "[확인 필요]", "[확인 필요]"], ["...", "...", "..."], ["3", "…", "…"],
            ["합계", "-", "-"], ["2", "중략", ""]]
    assert ww.drop_filler_rows(rows) == [["NO", "주요프로그램", "위치"], ["1", "[확인 필요]", "[확인 필요]"], ["합계", "-", "-"]]
    out, _ = ww.write_section(_form(), "절:가. 전시 부문",
                              ww.parse_body("1) 배치\n| NO | 위치 |\n|---|---|\n| 1 | A구역 |\n| ... | ... |"))
    x = _xml(out)
    assert "A구역" in x and "<hp:t>...</hp:t>" not in x


# ---- 4. 절 사이 되풀이 ----

OVERVIEW = "1) 2018 행사는 2018. 6. 25.(월) ~ 6. 26.(화) 2일간 경주드림센터에서 개최되었으며 총 281명이 참가함.\n" \
           "2) 본 행사는 사회맞춤형 육성사업의 일환으로 창의인재 양성과 취업 마인드 제고를 목적으로 추진함."


def test_repeated_lines_detects_overview_copied_between_sections():
    other = OVERVIEW + "\n3) 전시 부문은 성과물 12점을 전시함."
    again = ("1) 2018 행사는 2018. 6. 25.(월)~6. 26.(화) 2일간 경주드림센터에서 개최되었으며, 총 281명이 참가함(출처: 결과보고 1쪽).\n"
             "2) 본 행사는 사회맞춤형 육성사업의 일환으로 창의인재 양성 및 취업 마인드 제고를 목적으로 추진함.\n"
             "3) 프로그램 부문은 특강 2회와 TRIZ 실습으로 운영함.")
    hits = wa.repeated_lines(again, [other])
    assert len(hits) == 2 and not any("TRIZ" in h for h in hits)
    assert wa.repeated_lines("3) 프로그램 부문은 특강 2회와 TRIZ 실습으로 운영함.", [other]) == []
    # 날짜 「2018. 6.」 에서 문장을 자르지 않는다 · 짧은 머리 줄(사업 이름)만 같은 것은 되풀이가 아니다
    assert len(wa._sentences(OVERVIEW)) == 2
    assert wa.repeated_lines("1) 2018 YNC Festival 전시 개요", ["1) 2018 YNC Festival 홍보 개요"]) == []


def test_main_write_returns_repeat_once_then_accepts(tmp_path, monkeypatch):
    """메인 직접 쓰기 — 다른 절과 같은 문장이 둘 이상이면 한 번 돌려주고, 같은 field 로 다시 쓰면 넣는다."""
    form = _form()
    data, _ = ww.write_section(form, "절:가. 전시 부문", ww.parse_body(OVERVIEW))
    p = tmp_path / "w.hwpx"
    p.write_bytes(data)
    hw = wa.HwpTools.__new__(wa.HwpTools)
    hw.db = None
    hw.repeat_notes = set()
    monkeypatch.setattr(hw, "_guard", lambda: (7, {"path": str(p)}), raising=False)
    monkeypatch.setattr(hw, "resolve", lambda name, kinds=None: name, raising=False)
    monkeypatch.setattr(hw, "room_problem", lambda field, text, rooms=None: None, raising=False)
    monkeypatch.setattr(hw, "gate", lambda text, by="메인": None, raising=False)
    got = []

    class _Stop(Exception):
        pass

    def _holder(*a, **k):
        got.append("passed")
        raise _Stop
    from zzaimy.app import webhwp_coedit as ce
    monkeypatch.setattr(ce, "holder", _holder)
    r = hw.write({"field": "절:나. 프로그램 부문", "text": OVERVIEW + "\n3) 특강을 운영함."})
    assert r.startswith("쓰지 않았다") and "거의 같은 줄 2개" in r and not got
    try:
        hw.write({"field": "절:나. 프로그램 부문", "text": OVERVIEW + "\n3) 특강을 운영함."})
    except _Stop:
        pass
    assert got == ["passed"]                                  # 두 번째는 관문을 지나 쓰기로 간다
    # 서브 쓰기는 돌려주지 않는다(자기 점검·보고에서 알린다)
    hw.repeat_notes = set()
    got.clear()
    try:
        hw.write({"field": "절:나. 프로그램 부문", "text": OVERVIEW}, by="서브")
    except _Stop:
        pass
    assert got == ["passed"]


def test_new_section_gets_guide_box_even_after_all_boxes_removed():
    """다 쓴 절의 상자를 모두 뗀 뒤에도 새 절 넣기(section_insert)는 작성방법 상자를 만든다 — 본뜰 상자가 없으면 문서 표 모양으로."""
    from zzaimy.app import webhwp_edit_ops as eo
    form = _form()
    out, _ = ww.write_section(form, "절:가. 전시 부문", ww.parse_body("1) 전시 운영함"))
    out, _ = ww.write_section(out, "절:나. 프로그램 부문", ww.parse_body("1) 특강 운영함"))
    out, _ = ww.drop_guides(out, [k for k, v in ww.field_values(out).items() if v.strip()])
    assert "【작성방법】" not in _xml(out)
    new, _msg = eo.section_insert(out, {"title": "성과 지표", "guide": "성과 지표와 목표값을 쓴다."})
    ctx = ww.section_context(new, "절:다. 성과 지표")
    assert "【작성방법】 성과 지표와 목표값을 쓴다." in ctx and eo.check_bytes(new) == []
