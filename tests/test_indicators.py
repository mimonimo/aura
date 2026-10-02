import json

from zzaimy.graph import indicators as I


def _table(cells):
    return json.dumps({"cells": [[r, c, rs, cs, 0, t] for r, c, rs, cs, t in cells]}, ensure_ascii=False)


def test_multi_row_header_reads_round_targets():
    t = _table([
        (0, 0, 2, 1, "구분"), (0, 1, 2, 1, "성과지표명"), (0, 2, 2, 1, "기준값"), (0, 3, 1, 2, "목푯값"),
        (1, 3, 1, 1, "1차년도"), (1, 4, 1, 1, "2차년도"),
        (2, 0, 1, 1, "핵심"), (2, 1, 1, 1, "[핵심] 졸업생 지역 정주 취업률(%)"), (2, 2, 1, 1, "30.1"), (2, 3, 1, 1, "31.0"), (2, 4, 1, 1, "32.5 %"),
    ])
    obs = I.from_table(t)
    assert {(o.measure, o.period, o.value) for o in obs} == {("baseline", "", 30.1), ("target", "1차", 31.0), ("target", "2차", 32.5)}
    assert all(o.indicator == "졸업생 지역 정주 취업률(%)" and o.tag == "핵심" and o.group == "핵심" and o.unit for o in obs)


def test_text_cells_and_scale_items_are_not_values():
    t = _table([
        (0, 0, 1, 1, "성과지표"), (0, 1, 1, 1, "목표"), (0, 2, 1, 1, "실적값"),
        (1, 0, 1, 1, "기여도"), (1, 1, 1, 1, "•대구시 D5 신산업 수요와 연계"), (1, 2, 1, 1, "③ 보통"),
        (2, 0, 1, 1, "참여 기업 수"), (2, 1, 1, 1, "120개"), (2, 2, 1, 1, "131개"),
    ])
    obs = I.from_table(t)
    assert [(o.indicator, o.measure, o.value, o.unit) for o in obs] == [("참여 기업 수", "target", 120.0, "개"), ("참여 기업 수", "actual", 131.0, "개")]


def test_year_labels_and_name_key():
    assert I.period_of("연차별 목푯값/‘28") == "2028" and I.period_of("2025년 실적") == "2025" and I.period_of("3차년도") == "3차"
    assert I.name_key("[자율②] 평생교육 프로그램 수혜자 만족도") == I.name_key("평생교육 프로그램  수혜자만족도(점)")
    assert I.clean_name("3D 프린팅 이수자 수") == "3D 프린팅 이수자 수"


def test_table_without_indicator_header_is_ignored():
    t = _table([(0, 0, 1, 1, "조직"), (0, 1, 1, 1, "인력"), (1, 0, 1, 1, "사업단"), (1, 1, 1, 1, "6명")])
    assert I.from_table(t) == []
