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


def test_agent_decided_depth_overrides_word_match(monkeypatch):
    """11-25: 낱말 맞추기(「며칠」「언제까지」)가 한 줄 답에도 생각을 켜 몇 분씩 — 에이전트가 정한 깊이를 먼저 따른다."""
    from zzaimy.app import effort, responder as R
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    monkeypatch.setattr(effort, "thinking", lambda: False)
    monkeypatch.setattr(effort, "current", lambda: effort.DEEP)
    q = "출장 끝나고 며칠 안에 정산해야 해?"
    assert R.answer_think(q) is True                      # 낱말만 보면 켠다
    assert R.answer_think(q, False) is False and R.answer_think("안녕", True) is True
