from datetime import date

from zzaimy.app.date_facts import date_facts, dates_in


def test_date_facts_gives_weekday_and_span_ends():
    t = date_facts("12월 3일에 출장이 끝나면 여비 정산은 언제까지 해야 해?", today=date(2026, 11, 25))
    assert "오늘: 2026년 11월 25일(수)" in t
    assert "2026년 12월 3일(목)" in t and "다음 날 2026년 12월 4일(금)" in t
    assert "14일 2026년 12월 17일(목)" in t and "그달 말일 2026년 12월 31일(목)" in t


def test_dates_in_reads_full_and_short_forms():
    today = date(2026, 11, 25)
    assert dates_in("2025.3.1 부터 4월 30일까지", today) == [date(2025, 3, 1), date(2026, 4, 30)]
    assert dates_in("2월 30일", today) == [] and date_facts("날짜 없는 질문", today) == ""
