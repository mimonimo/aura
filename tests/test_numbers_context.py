"""수치 맥락 대조(11-20) — 같은 숫자가 근거 어딘가에 있는 것만으로는 근거가 아니다.
숫자 뒤 셈 단위(글에서 떼어 낸 것)가 맞아야 하고, 10 이하의 셈 숫자는 근거 쪽 둘레 낱말도 겹쳐야 한다.
담당자가 직접 준 글(given)과 「◇ 집계」 줄은 숫자+단위만 맞으면 근거."""
from zzaimy.verify.numbers import given, mask_unsupported


def bad(text, ev):
    return mask_unsupported(text, ev, context=True)[1]


def test_grade_from_date_or_other_count_blocked():
    assert bad("가) 대상: 2학년", ["사업 기간 2026. 2. 1. ~ 2026. 12. 31."]) == ["2학년"]
    assert bad("가) 대상: 2학년", ["참여 학과: 2개 학과"]) == ["2학년"]
    assert bad("가) 대상: 2학년", [given("2026. 2. 1. 시작")]) == ["2학년"]      # 담당자 글이라도 단위가 다르면


def test_same_unit_needs_shared_word_in_documents():
    # 근거 문서의 「팀 4명」 은 팀 이야기 — 같은 낱말(팀)을 쓰면 근거, 「참여 인원」 으로 바꿔 쓰면 막는다
    assert bad("팀 인원 4명", ["팀 4명으로 운영"]) == []
    assert bad("참여 인원 4명", ["팀 4명"]) == ["4명"]
    # 담당자가 준 글이면 숫자+단위만 맞으면 된다
    assert bad("참여 인원 4명", [given("팀 4명")]) == []


def test_total_count_from_each_weeks_blocked():
    assert bad("방학 중 총 4회 운영", [given("여름·겨울 각 2주 운영")]) == ["4회"]
    assert bad("여름·겨울 각 2주간 운영", [given("여름·겨울 각 2주")]) == []


def test_staff_request_value_allowed_and_unit_mismatch_blocked():
    assert bad("참여 인원 30명", [given("인원 30명")]) == []
    assert bad("참여 인원 30명", ["작년 30개 기관 참여"]) == ["30명"]
    assert bad("참여 인원 30명", ["| 구분 | 인원 |\n| 학생 | 30 |"]) == []      # 단위 없는 근거 값은 예전처럼


def test_budget_table_with_declared_unit_still_allowed():
    t = "<표> 예산 (단위 : 백만 원)\n| 비목 | 금액 | 비율 |\n| 인건비 | 120 | 24% |\n| 합계 | 500 | 100% |"
    ev = ["총 사업비 5억 원, 인건비 1억 2천만 원, 인건비 비율 24%"]
    assert bad(t, ev) == mask_unsupported(t, ev)[1] and "500" not in bad(t, ev) and "120" not in bad(t, ev)
    assert bad("사업비 500(백만원)", ["5억 원"]) == []


def test_structure_dates_and_one_untouched():
    assert bad("3대 전략을 2단계로 추진, 3월 중 착수, 2개 분야", ["없음"]) == []
    assert bad("연 1회 점검", ["없음"]) == []


def test_table_header_unit_counts():
    t = "| 구분 | 횟수(회) |\n|---|---|\n| 캠프 | 4 |"
    assert bad(t, ["없음"]) == ["4회"]
    assert bad(t, [given("캠프 4회")]) == []


def test_ledger_aggregate_and_derived_still_allowed():
    assert bad("참가자 7명", ["◇ 집계 합계 · 참가자: 3 + 4 = 7명"]) == []
    assert bad("1회당 3명 × 2회 = 6명", [given("1회당 3명, 2회")]) == []          # 맞게 셈한 결과(회·주·건 같은 셈 단위 식도)
    assert bad("3명 × 2회 = 7명", [given("1회당 3명, 2회")]) == ["7명"]


def test_off_by_default_for_other_callers_and_setting(monkeypatch):
    from zzaimy.verify.numbers import context_check_on
    assert mask_unsupported("가) 대상: 2학년", ["2026. 2. 1."])[1] == []         # context 를 주지 않은 호출부(대화 답 등)는 예전 그대로
    monkeypatch.setenv("ZZAIMY_NUMBER_CONTEXT", "0")
    assert context_check_on() is False
    monkeypatch.delenv("ZZAIMY_NUMBER_CONTEXT")
    assert context_check_on() is True


def test_check_body_routes_to_confirm_needed():
    from zzaimy.app.webhwp_agent import check_body
    why = check_body("가) 대상: 2학년\n나) 방학 중 총 4회 운영", [given("여름·겨울 각 2주, 2026. 2. 1. 시작")], 262144)
    assert why and "2학년" in why and "4회" in why and "확인 필요" in why
    assert check_body("가) 여름·겨울 각 2주 운영", [given("여름·겨울 각 2주")], 262144) is None
