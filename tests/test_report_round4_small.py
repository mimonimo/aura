"""결과보고서 4차 시험에서 찾은 작은 것들 — 끝 「|」 빠진 표 머리줄, 쉼표 없는 큰 수 알림."""
from zzaimy.app import webhwp_write as ww


def test_table_header_without_trailing_pipe_stays_in_table():
    items = ww.parse_body("요약\n\n| 성과지표 | 목표 | 실적 | 달성률(%)\n|---|---|---|---|\n| 교육 | 300 | 264 | 88.0 |\n\n끝")
    assert items[1] == ("table", [["성과지표", "목표", "실적", "달성률(%)"], ["교육", "300", "264", "88.0"]])
    assert ww.parse_body("| 구분 이라는 글\n다음 줄") == [("text", "| 구분 이라는 글\n다음 줄")]   # 표가 아닌 「|」 글은 그대로


def test_table_number_gate_names_the_offending_cell():
    # 11-22 5차: 거절 문구가 어느 칸인지 말하지 않아 모델이 같은 값을 되풀이해 넣었다
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.verify.numbers import mask_unsupported

    def gate(md):
        _, bad = mask_unsupported(md, ["4월 파이썬 캠프 참여 인원은 학생 42명이다."], context=True)
        return ("근거에 없는 수치: " + ", ".join(bad) + " — 모르는 값은 확인 필요") if bad else None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    a = {"op": "set_cells", "table": 9, "start": [2, 1],
         "values": [["4월", "파이썬 캠프", "학생 42명"], ["5월", "교과목 개발", "학생 57명"]]}
    why = t._numbers_check(1, "hwp_table_edit", a)
    assert why and "「5월」 행의 「학생 57명」" in why and "4월" not in why.split("걸린 칸:")[1]


def test_replace_is_number_gated_only_for_new_numbers():
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.verify.numbers import mask_unsupported

    def gate(md):
        _, bad = mask_unsupported(md, ["총 사업비는 300,000,000원이다."], context=True)
        return ("근거에 없는 수치: " + ", ".join(bad) + " — 모르는 값은 확인 필요") if bad else None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    why = t._numbers_check(1, "hwp_replace", {"find": "총 집행액은 확인 필요이다.", "replace": "총 집행액은 108,600,000원이다."})
    assert why and "108,600,000" in why
    assert t._numbers_check(1, "hwp_replace", {"items": [{"find": "2025 가상 사업 7명", "replace": "가상 사업 7명"}]}) is None
