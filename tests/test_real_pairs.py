"""실문서 학습쌍 — 양식 격자 × 완성본 표 대응, 사실 목록, 절 쌍의 수치 규칙."""

import json

from zzaimy.dataset import real_pairs as rp
from tests.test_hwpx_fill import _hwpx


def _grid(rows, spans=None, covered=None):
    return {"n": 1, "rows": rows, "covered": covered or set(), "spans": spans or {}}


def _kpi_form():
    # 양식 총괄표: 두 줄 머리(연차별 목푯값 → 1차년도·2차년도), 행마다 병합 폭이 달라 값 칸의 열 번호가 어긋난다
    rows = [
        ["핵심 성과지표명", "", "", "", "단위", "", "기준값", "연차별 목푯값", "", "", "", ""],
        ["", "", "", "", "", "", "", "1차년도", "", "2차년도", "", ""],
        ["① AI 기초 이수율", "", "", "", "%", "", "", "", "", "", "", ""],
        ["① -1.", "AI 기초 개발 지수", "", "건", "", "", "", "", "", "", "", ""],
    ]
    spans = {(0, 0): (0, 3), (0, 4): (4, 5), (0, 6): (6, 6), (0, 7): (7, 11), (1, 7): (7, 8), (1, 9): (9, 11),
             (2, 0): (0, 3), (2, 4): (4, 5), (2, 6): (6, 6), (2, 7): (7, 8), (2, 9): (9, 11),
             (3, 0): (0, 0), (3, 1): (1, 2), (3, 3): (3, 4), (3, 5): (5, 6), (3, 7): (7, 8), (3, 9): (9, 11)}
    covered = {(0, 1), (0, 2), (0, 3), (0, 5), (0, 8), (0, 9), (0, 10), (0, 11),
               (1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 8), (1, 10), (1, 11),
               (2, 1), (2, 2), (2, 3), (2, 5), (2, 8), (2, 10), (2, 11),
               (3, 2), (3, 4), (3, 6), (3, 8), (3, 10), (3, 11)}
    return _grid(rows, spans, covered)


def _kpi_done():
    cells = [[0, 0, 2, 3, 0, "지표명"], [0, 3, 2, 1, 0, "단위"], [0, 4, 2, 2, 0, "기준값"], [0, 6, 1, 3, 0, "목푯값"],
             [1, 6, 1, 1, 0, "2026년"], [1, 7, 1, 2, 0, "2027년"],
             [2, 0, 1, 3, 0, "① AI 기초 이수율"], [2, 3, 1, 1, 0, "%"], [2, 4, 1, 2, 0, "4.6"], [2, 6, 1, 1, 0, "18.3"], [2, 7, 1, 2, 0, "24.5"],
             [3, 0, 1, 3, 0, "①-1. AI 기초 개발 지수"], [3, 3, 1, 1, 0, "건"], [3, 4, 1, 2, 0, "5.8"], [3, 6, 1, 1, 0, "9.5"], [3, 7, 1, 2, 0, "12.3"]]
    return rp.table_cells(json.dumps({"n_rows": 4, "n_cols": 9, "cells": cells}))


def test_column_groups_use_lowest_header_row_and_mark_blank_columns():
    groups, head_n = rp.column_groups(rp._grid_cells(_kpi_form()))
    assert head_n == 2
    labels = [(g["label"], g["x0"], g["x1"]) for g in groups]
    assert ("1차년도", 7, 8) in labels and ("2차년도", 9, 11) in labels
    assert next(g for g in groups if g["label"] == "기준값")["blank"]
    assert not next(g for g in groups if g["label"] == "단위")["blank"]


def test_fill_maps_by_row_name_and_column_span_not_by_index():
    cells = rp._fill_from_table(_kpi_form(), _kpi_done())
    got = {(c["row"], c["col"]): c["text"] for c in cells}
    # 2행(① 이수율): 기준값 c6, 1차년도 c7, 2차년도 c9 — 3행(①-1)은 병합이 달라 c5·c7·c9
    assert got[(2, 6)] == "4.6" and got[(2, 7)] == "18.3" and got[(2, 9)] == "24.5"
    assert got[(3, 5)] == "5.8" and got[(3, 7)] == "9.5" and got[(3, 9)] == "12.3"
    assert len(cells) == 6


def test_fill_handles_text_values_and_rowless_tables():
    # 추진체계표: 이름 열 없이 빈 칸만 — 같은 순번의 행에 글 값을 넣는다
    form = _grid([["위원회", "역할", "구성"], ["", "", ""], ["", "", ""]])
    done = rp.table_cells(json.dumps({"n_rows": 3, "n_cols": 3, "cells": [
        [0, 0, 1, 1, 0, "위원회"], [0, 1, 1, 1, 0, "역할"], [0, 2, 1, 1, 0, "구성"],
        [1, 0, 1, 1, 0, "운영위원회"], [1, 1, 1, 1, 0, "총괄 심의"], [1, 2, 1, 1, 0, "총장 외 7인"],
        [2, 0, 1, 1, 0, "실무협의회"], [2, 1, 1, 1, 0, "집행"], [2, 2, 1, 1, 0, "단장 외 5인"]]}))
    got = {(c["row"], c["col"]): c["text"] for c in rp._fill_from_table(form, done)}
    assert got[(1, 0)] == "운영위원회" and got[(2, 2)] == "단장 외 5인" and len(got) == 6


def test_fact_sheet_labels_table_values_and_keeps_numbers_inside_text_cells():
    facts = rp.fact_sheet([("table", _kpi_done()), ("text", "재학생 1,200명이 2025년에 참여했다.")])
    assert any("AI 기초 개발 지수" in f and "2026년" in f and "9.5" in f for f in facts)
    assert any("1,200명" in f for f in facts)
    prog = rp.table_cells("구분 | 추진실적\nAI 지침서 [증빙 2-35] | 교수법 개발 32건")
    facts = rp.fact_sheet([("table", prog)])
    assert any("2-35" in f for f in facts) and any("32건" in f for f in facts)


def test_form_model_reads_headings_instructions_and_grids(tmp_path):
    secs = rp.form_model(_hwpx(tmp_path))
    heads = [s.heading for s in secs]
    assert heads == ["1. 사업 개요", "1.1. 대학의 여건 분석", "1.2. 특성화 방향"]
    assert "작성방법" in secs[1].instructions and not secs[1].grids
    assert len(secs[2].grids) == 1 and secs[2].grids[0]["rows"] == [["구분", "값"]]


def test_section_pair_is_serving_shaped_and_numbers_come_from_input(tmp_path):
    secs = rp.form_model(_hwpx(tmp_path))
    sec = secs[1]
    parts = [("text", "재학생은 1,000명이며 취업률은 72.5% 이다."), ("table", {"rows": [["구분", "값"], ["재학생", "1,000"]], "cells": rp.table_cells("구분 | 값\n재학생 | 1,000")})]
    pr = rp.build_section_pair(sec, secs, parts, [{"reg_title": "평가편람", "content": "여건 분석 배점 10"}], title="계획서")
    plan = json.loads(pr["output"])
    assert plan["ops"][0]["op"] == "insert" and plan["ops"][0]["section"] == sec.index
    assert plan["ops"][1]["op"] == "table" and "재학생 | 1,000" in plan["ops"][1]["text"]
    assert pr["missing_numbers"] == []
    assert "[담당자 지시]" in pr["human"] and "[이 절의 양식 안내·작성방법]" in pr["human"] and "72.5%" in pr["human"]
    assert "1,000명" in pr["shown"]
    assert "재학생은 1,000명이며 취업률은 72.5% 이다." in pr["human"]          # 수치 없는 사실도 문장째 입력에(C-134)


def test_sentence_facts_keep_leading_numbers_but_drop_numbering():
    facts = rp.sentence_facts("1) 100조원 규모 투자 기반 조성\n• 82개 교과목 지침서 개발\n(2) 2025년 12월 컨트롤타워 신설")
    assert facts == ["100조원 규모 투자 기반 조성", "82개 교과목 지침서 개발", "2025년 12월 컨트롤타워 신설"]


def test_fact_numbers_units_and_section_forms():
    assert rp.fact_numbers("참여자 9명, 절 1.2, Step 1, 3-Tier, 2026년, 12.5%, 1,250백만원", strict=False) == {"9", "2026", "12.5%", "1250"}
    assert "27" in rp.fact_numbers("위원 27") and "27" not in rp.fact_numbers("위원 27", strict=False)


def test_section_parts_keep_table_cells_with_spans():
    secs = [rp.FormSection(index=1, heading="1.1. 대학의 여건 분석", level=2)]
    chunks = [{"seq": 1, "page_no": 1, "kind": "text", "content": "1.1. 대학의 여건 분석\n본문 한 줄"},
              {"seq": 2, "page_no": 1, "kind": "table", "content": json.dumps({"n_rows": 2, "n_cols": 2, "cells": [[0, 0, 1, 2, 0, "병합 칸"], [1, 0, 1, 1, 0, "가"], [1, 1, 1, 1, 0, "나"]]})}]
    plan = rp.section_parts(secs, chunks)
    assert plan[0]["matched"] and plan[0]["tables"] == 1
    tbl = next(p for k, p in plan[0]["parts"] if k == "table")
    assert tbl["cells"][0] == (0, 0, 1, 2, "병합 칸")


def test_figure_spec_from_image_text_and_none_for_logos():
    spec = rp.figure_spec("글자:\n국가 정책 동향\n• AI 3개 강국 도약\n• 100만 디지털 인재\n대구시 AX 전략\n• AX 수도 대구 선언\n• AI 인프라 투자\n\n설명:\n도식이다.")
    assert spec and spec["layout"] == "cards" and [b["title"] for b in spec["blocks"]] == ["국가 정책 동향", "대구시 AX 전략"]
    assert spec["blocks"][0]["items"] == ["AI 3개 강국 도약", "100만 디지털 인재"]
    assert rp.figure_spec("글자:\nY\nNC\n\n설명:\n로고다.") is None
    # 글머리는 첫 줄에만, 이어지는 줄과 출처 줄은 앞 요점에 붙는다
    spec = rp.figure_spec("글자:\n국가 정책 동향\n• AI 3개 강국 도약\n100조원 투자 기반 조성\n출처: 국정기획위원회, 2025\n• 디지털 인재양성\n대구시 AX 전략\n• AX 수도 대구\n\n설명:\n도식")
    assert [b["title"] for b in spec["blocks"]] == ["국가 정책 동향", "대구시 AX 전략"]
    assert spec["blocks"][0]["items"][0].startswith("AI 3개 강국 도약 100조원 투자 기반 조성 (출처")


def test_section_parts_turn_image_text_into_figure_and_stop_at_appendix():
    secs = [rp.FormSection(index=1, heading="1.1. 대학의 여건 분석", level=2)]
    chunks = [{"seq": 1, "page_no": 1, "kind": "text", "content": "1.1. 대학의 여건 분석\n본문 한 줄"},
              {"seq": 2, "page_no": 1, "kind": "image", "content": "image_003.png"},
              {"seq": 3, "page_no": 1, "kind": "image_text", "content": "글자:\n정책 동향\n• 항목 하나\n• 항목 둘\n산업 수요\n• 항목 셋\n\n설명:\n도식"},
              {"seq": 4, "page_no": 1, "kind": "table", "content": json.dumps({"n_rows": 1, "n_cols": 2, "cells": [[0, 0, 1, 1, 0, ""], [0, 1, 1, 1, 0, "대외여건분석"]]})},
              {"seq": 5, "page_no": 2, "kind": "text", "content": "증빙자료\n증빙 목차 첫 줄 3건"}]
    plan = rp.section_parts(secs, chunks)
    kinds = [k for k, _ in plan[0]["parts"]]
    assert kinds == ["text", "figure", "text"] and plan[0]["figures"] == 1 and plan[0]["tables"] == 0
    assert plan[0]["parts"][2][1] == "대외여건분석"                    # 띠 표는 소제목 줄로
    assert "image_003" not in json.dumps(plan[0]["parts"], ensure_ascii=False) and "증빙 목차" not in json.dumps(plan[0]["parts"], ensure_ascii=False)


def test_label_columns_are_not_copied_into_blank_cells():
    # 양식 이름 칸에 있는 글(혁신지원·RISE)로 찬 완성본 열은 값 열이 아니다
    form = _grid([["사업", "’25년", "’26년"], ["혁신지원", "", ""], ["RISE", "", ""]])
    done = rp.table_cells("사업 | 구분 | ’25년 | ’26년\n혁신지원 | 혁신지원 | 10 | 20\nRISE | RISE | 5 | 6")
    got = {(c["row"], c["col"]): c["text"] for c in rp._fill_from_table(form, done)}
    assert got == {(1, 1): "10", (1, 2): "20", (2, 1): "5", (2, 2): "6"}
