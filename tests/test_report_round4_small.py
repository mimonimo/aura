"""결과보고서 4차 시험에서 찾은 작은 것들 — 끝 「|」 빠진 표 머리줄, 쉼표 없는 큰 수 알림."""
from zzaimy.app import webhwp_write as ww


def test_table_header_without_trailing_pipe_stays_in_table():
    items = ww.parse_body("요약\n\n| 성과지표 | 목표 | 실적 | 달성률(%)\n|---|---|---|---|\n| 교육 | 300 | 264 | 88.0 |\n\n끝")
    assert items[1] == ("table", [["성과지표", "목표", "실적", "달성률(%)"], ["교육", "300", "264", "88.0"]])
    assert ww.parse_body("| 구분 이라는 글\n다음 줄") == [("text", "| 구분 이라는 글\n다음 줄")]   # 표가 아닌 「|」 글은 그대로
