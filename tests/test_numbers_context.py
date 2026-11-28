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


def test_kpi_row_from_aggregate_and_given_target_passes():
    """집계 줄(「…=9건」)과 담당자가 준 목표(10건)로 채운 성과지표 행 — 실적·달성률이 막히지 않는다(11-21 결과보고서 3차)."""
    from zzaimy.verify import numbers as N
    ev = ["◇ 집계 합계 · 산업체 협약: 4+3+2=9건", "◇ 집계 합계 · 취업 연계: 6+11+14=31명",
          N.given("목표는 산업체 협약 10건, 취업 연계 40명")]
    table = ("| 성과지표 | 목표 | 실적 | 달성률(%) |\n|---|---|---|---|\n"
             "| 산업체 협약(건) | 10건 | 9건 | 90.0% |\n| 취업 연계(명) | 40명 | 31명 | 77.5 |")
    out, masked = N.mask_unsupported(table, ev, context=True)
    assert masked == [] and out == table
    bad = "| 성과지표 | 목표 | 실적 | 달성률(%) |\n|---|---|---|---|\n| 산업체 협약(건) | 10건 | 9건 | 95.0% |"
    assert N.mask_unsupported(bad, ev, context=True)[1]                     # 틀린 달성률은 그대로 막는다


def test_row_ratio_with_plain_small_numbers():
    # 11-22 6차: 단위 없는 「300」「264」 가 비율 칸으로 잘못 읽혀 달성률 88.0% 가 막혔다
    from zzaimy.verify.numbers import mask_unsupported
    md = "| 성과지표 | 목표 | 실적 | 달성률(%) |\n| --- | --- | --- | --- |\n| 교육 이수 인원 | 300 | 264 | 88.0% |\n| 취업 연계 | 40 | 31 | 80.0% |"
    _, bad = mask_unsupported(md, ["목표 교육 이수 인원 300명, 취업 연계 40명. 실적 264명, 31명"], context=True)
    assert "88.0" not in bad and "80.0" in bad


def test_thousand_won_table_matches_aggregate_and_won_column():
    # 11-23 묶음 B: 사업비 표가 천원 단위인데 근거(집계 줄·엑셀 「지출액(원)」 열)의 값이 「원」 없이 적혀 금액으로 읽히지 않았다
    from zzaimy.verify import numbers as n
    agg = "◇ 집계 홍보비 · 지출액: 450,000+9,350,000=9,800,000 (근거: 《지출 내역》)\n◇ 집계 합계 · 지출액: 51,600,000"
    _, bad = n.mask_unsupported("| 세목 | 집행액(천원) |\n| --- | --- |\n| 홍보비 | 9,800 |\n| 합계 | 51,600 |", [agg])
    assert bad == []
    xls = "지출일자 | 추진 과제 | 세목 | 내용 | 지출액(원)\n2026.04.07 | 지역 연계 강좌 | 홍보비 | 강좌 | 450000"
    _, bad = n.mask_unsupported("| 세목 | 집행액(천원) |\n| --- | --- |\n| 홍보비 | 450 |\n| 운영비 | 52 |", [xls])
    assert bad == ["52"]
    _, bad = n.mask_unsupported("| 과제 | 참석자(명) |\n| --- | --- |\n| 강좌 | 9,800 |", [agg])
    assert bad == ["9,800"]                                  # 금액 열이 아니면 단위 바꿈으로 맞추지 않는다


def test_section_number_references_pass_but_are_not_value_evidence():
    # 11-23: 본문의 「2.2표」「2.1및」 이 맥락 대조에 막혔다 — 절을 가리키는 말이다. 거꾸로 절 번호는 「2.2%」 의 근거가 아니다
    from zzaimy.verify import numbers as n
    ev = ["2. 추진 실적\n2.1 월별 추진 실적\n2.2 분야별 추진 실적\n만족도 4.2점"]
    assert n.mask_unsupported("월별은 2.1, 분야별은 2.2표에 정리함", ev, context=True)[1] == []
    assert n.mask_unsupported("2.1및 2.2에 정리함", ev, context=True)[1] == []
    assert n.mask_unsupported("달성률 2.2%", ev, context=True)[1] == ["2.2"]
    assert n.mask_unsupported("만족도 4.2점", ev, context=True)[1] == []


def test_table_total_row_passes_when_it_is_the_column_sum():
    # 11-23 시연 04: 프로그램별 인원은 넣고 합계만 「확인 필요」 — 열의 합과 맞는 합계는 셈한 값이다(틀린 합계는 막힌다)
    from zzaimy.verify import numbers as n
    ev = ["참여 인원 42명, 55명, 60명"]
    d = "| 프로그램 | 참여 인원(명) |\n| --- | --- |\n| 캠프 | 42 |\n| 특강 | 55 |\n| 세미나 | 60 |\n| 합계 | 157 |"
    assert n.mask_unsupported(d, ev, context=True)[1] == []
    assert n.mask_unsupported(d.replace("157", "158"), ev, context=True)[1] == ["158"]


def test_short_dates_from_evidence_pass_but_invented_dates_do_not():
    ev = ["지역 역사 탐방 강좌 운영일 | 2026년 11월 10일", "디지털 강좌 2026.4.7 참석자 30명"]
    _, bad = mask_unsupported("역사 탐방 강좌(11.10.), 디지털 강좌(4.7.) 운영", ev, context=True)
    assert bad == []
    _, bad = mask_unsupported("역사 탐방 강좌(12.15.) 운영", ev, context=True)
    assert bad == ["12.15"]


def test_aggregate_count_with_ge_matches_hoe_in_same_row():
    ev = ["  지역 연계 강좌: 기록 14개, 활동 5개, 참석자(명) 190명\n  성인 학습자 지원: 기록 9개, 활동 3개, 참석자(명) 27명"]
    table = "| 추진 과제 | 운영 횟수 | 참석자(명) |\n| 지역 연계 강좌 | 5회 | 190 |\n| 성인 학습자 지원 | 3회 | 27 |"
    assert mask_unsupported(table, ev, context=True)[1] == []
    # 사람 수는 「개」 와 같은 갈래가 아니다
    assert mask_unsupported("| 추진 과제 | 참여 |\n| 지역 연계 강좌 | 5명 |", ev, context=True)[1] == ["5명"]


def test_won_amount_written_into_thousand_won_column_is_caught():
    # 11-23 3차 녹화 01: 세목별 집행액(천원) 표에 근거의 원 금액 9,800,000 을 그대로 넣음 — 천 배
    from zzaimy.verify.numbers import mask_unsupported
    ev = ["2026.04.07 | 지역 연계 강좌 | 홍보비 | 생활 속 디지털 기초 강좌 | 3150000",
          "◇ 집계 홍보비 · 집행액(천원): 3,150,000+6,650,000=9,800,000"]
    bad = "| 세목 | 집행액(천원) |\n|---|---|\n| 홍보비 | 9,800,000 |"
    good = "| 세목 | 집행액(천원) |\n|---|---|\n| 홍보비 | 9,800 |"
    _, v_bad = mask_unsupported(bad, ev)
    _, v_good = mask_unsupported(good, ev)
    assert v_bad and not v_good
    # 원 단위 열에 원 금액은 그대로 받는다
    _, v_won = mask_unsupported("| 세목 | 집행액(원) |\n|---|---|\n| 홍보비 | 9,800,000 |", ev)
    assert not v_won


def test_scale_slips_points_at_evidence_amount_off_by_powers_of_ten():
    # 26차 B 한번에: 「1억 2천만 원」 을 12,000,000원 으로 옮겨 관문에 다섯 번 막힘 — 자릿수만 다른 근거 금액을 보인다
    from zzaimy.verify.numbers import scale_slips
    ev = ["총 사업비는 1억 2천만 원이다."]
    assert scale_slips(["12000000"], ev) == ["12000000 → 근거 금액 120,000,000원과 자릿수만 다르다, 근거대로면 「120,000,000원」"]
    # 천원 단위로 적은 값은 같은 단위로 고친 값을 보인다(40차 B 한번에)
    assert scale_slips(["12,000천"], ev)[0].endswith("근거대로면 「120,000천원」")
    assert scale_slips(["777", "33"], ev) == []


def test_check_body_shows_scale_slip():
    from zzaimy.app.webhwp_agent import check_body
    why = check_body("총 사업비는 12,000,000원이다.", ["총 사업비는 1억 2천만 원이다."], 100000)
    assert why and "단위 셈 확인" in why and "120,000,000원" in why


def test_scale_slips_reads_thousand_unit():
    # 31차 B 한번에: 1억 2천만 원을 「12,000천원」 으로 옮겨 막히는데 단위 셈 알림이 없었다
    from zzaimy.verify.numbers import scale_slips
    out = scale_slips(["12,000천"], ["총 사업비는 1억 2천만 원이다."])
    assert out and "120,000,000원(=120,000천원)" in out[0]
    assert scale_slips(["120,000천"], ["총 사업비는 1억 2천만 원이다."]) == []


def test_numbers_gate_fails_closed_when_work_doc_unreadable():
    # 32차 B 한번에: 근거를 읽다 난 오류에 관문이 통과로 돌아가 「12,000천원」 이 들어간 것으로 봄 — 작업본을 못 읽으면 그 글만 빼고 대 본다
    from zzaimy.app import webhwp_agent as wa
    hw = wa.HwpTools.__new__(wa.HwpTools)
    hw.evidence = lambda: ["총 사업비는 1억 2천만 원이다."]
    def _boom():
        raise OSError("saving")
    hw.form_text = _boom
    assert "12,000천" in (hw.numbers_gate("| 총 사업비 | 12,000천원 |") or "")
    assert hw.numbers_gate("| 총 사업비 | 120,000천원 |") is None
    hw.evidence = lambda: (_ for _ in ()).throw(RuntimeError())
    assert hw.numbers_gate("| 총 사업비 | 120,000천원 |") == wa.GATE_UNREAD


def test_gate_bad_keeps_numbers_apart_from_scale_note():
    """11-24 34차: 단위 셈 꼬리까지 한 수로 읽으면 표 고치기가 걸린 수를 이번 칸 값이 아닌 것으로 보고 통과시켰다."""
    from zzaimy.app.webhwp_edit_ops import gate_bad
    why = "근거에 없는 수치: 12,000,000, 51,600 [단위 셈 확인: 12,000,000 → 근거 금액 120,000,000원과 자릿수만 다르다] — 담당자가…"
    bad, rest = gate_bad(why)
    assert bad == ["12,000,000", "51,600"]
    assert rest.startswith(" [단위 셈 확인")
    assert gate_bad("근거에 없는 수치: 7 (든 줄: 7명) — x")[0] == ["7"]
    assert gate_bad("다른 글") is None


def test_stream_stops_long_thinking():
    """11-25: 한 줄 답에 생각 10,072토큰·421초 — 상한을 넘도록 답 글이 없으면 ThinkTooLong 으로 끊는다."""
    import time as _t
    from types import SimpleNamespace as NS
    from unittest import mock
    from zzaimy.app import responder as R

    def chunks():
        for _ in range(50):
            yield NS(usage=None, choices=[NS(delta=NS(content=None, reasoning_content="생각"), finish_reason=None)])
    clock = iter(range(0, 10000, 5))
    seen = []
    with mock.patch.object(R.llm_budget, "create_fitted", lambda *a, **k: chunks()), \
         mock.patch.object(_t, "monotonic", lambda: float(next(clock))):
        try:
            R.AgentResponder._stream(NS(model="m", connection_id=None), [], 100, [], None, lambda t: None,
                                     gen={"extra_body": {}}, on_progress=seen.append, think_cap=60)
            raised = False
        except R.ThinkTooLong:
            raised = True
    assert raised and any(x.startswith("생각하는 중") for x in seen)
