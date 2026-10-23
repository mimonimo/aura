

def test_unit_then_bare_number_is_one_value():
    """「1만 1799명」 = 11,799 — 근거와 초안의 표기가 달라도 같은 수로 보고 「근거 없음」으로 잡지 않는다."""
    from zzaimy.verify.numbers import canonical_values, verify_numbers
    assert "11799" in canonical_values("고용 1만 1799명")
    assert verify_numbers("고용 11,799명", ["고용 1만 1799명"]).violations == []
    assert "20000" in canonical_values("2만 명과 3명") and "20003" not in canonical_values("2만 명과 3명")


def test_mask_unsupported_keeps_units_years_and_supported():
    from zzaimy.verify.numbers import mask_unsupported
    text, hit = mask_unsupported("- 이수자 300명 이상, 연계율 80% 달성\n1. 2026년 2개년 계획, 662개사", ["기업 662개사"])
    assert text == "- 이수자 ○○명 이상, 연계율 ○○% 달성\n1. 2026년 2개년 계획, 662개사" and hit == ["300", "80"]


def test_agent_ops_numbers_masked_before_apply():
    from zzaimy.app.gdocs_agent import mask_ops_numbers
    ops = [{"op": "insert", "text": "목표 300명"}, {"op": "fill", "cells": [{"row": 1, "col": 1, "text": "45.7%"}]}]
    hit = mask_ops_numbers(ops, ["근거: 45.7%"])
    assert ops[0]["text"] == "목표 ○○명" and ops[1]["cells"][0]["text"] == "45.7%" and hit == ["300"]


def test_mask_unsupported_leaves_phone_and_dates():
    from zzaimy.verify.numbers import mask_unsupported
    text, hit = mask_unsupported("문의 010-9999-8888, 접수 2026.10.15, 비율 3:1", [])
    assert text == "문의 010-9999-8888, 접수 2026.10.15, 비율 3:1" and hit == []


def test_flag_unsupported_marks_instead_of_hiding():
    from zzaimy.verify.numbers import flag_unsupported
    ev = ["2026년 목표 취업률 72.5%, 참여 학생 300명, 예산 5억 원"]
    text, flagged = flag_unsupported("취업률 72.5%, 참여 350명, 예산 5억 원, 2027년 3대 전략 80% 목표", ev)
    assert "350명(근거 확인 필요)" in text and "80%(근거 확인 필요)" in text
    assert "72.5%(근거" not in text and "5억 원(근거" not in text and "2027년(근거" not in text and "3대(근거" not in text
    assert flagged == ["350명", "80"] or sorted(flagged) == sorted(["350명", "80"]) or len(flagged) == 2


def test_same_value_unit_changes_are_supported():
    """같은 값의 단위 바꿈(5억 = 500,000,000원 = 500백만원 = 500(백만원) = 단위가 백만원인 표의 500)은 근거 있는 값이다."""
    from zzaimy.verify.numbers import mask_unsupported, verify_numbers
    ev = ["예산은 5억 원 정도로 생각하고 있어."]
    for draft in ("총 사업비 500,000,000원", "총 사업비 500백만원", "총 사업비 500(백만원)", "총 사업비 5억원",
                  "| 구분 | 금액 |\n|---|---|\n| 총 사업비 | 500 |\n(단위: 백만원)", "총 사업비 500,000천원"):
        assert verify_numbers(draft, ev).violations == [], draft
        assert mask_unsupported(draft, ev)[1] == [], draft
    # 근거 쪽 선언 단위도 — 「(단위: 천원)」 표의 500,000 은 5억
    assert verify_numbers("총 사업비 5억 원", ["(단위: 천원)\n총 사업비 500,000"]).violations == []
    # 정말 없는 숫자는 그대로 잡는다
    assert mask_unsupported("총 사업비 700(백만원)", ev)[1] == ["700"]
    assert mask_unsupported("참여 학생 4,500명", ev)[1] == ["4,500"]
    assert verify_numbers("총 사업비 6억 원", ev).violations == ["6"]


def test_unit_declarations_cannot_launder_numbers():
    """보안: 단위 선언을 여러 개 넣어도 아무 숫자나 근거와 맞출 수 없다 — 금액 단위 바꿈은 금액끼리, 가장 가까운 선언 하나만."""
    from zzaimy.verify.numbers import verify_numbers
    ev = ["참여 학생 3,000명, 사업비 5억 원"]
    bad = "(단위: 천원) (단위: 만원) (단위: 억원)\n참여 학생 3명으로 운영"
    assert not verify_numbers(bad, ev).ok                       # 3×천 = 3000(사람 수)에 맞추지 못함
    assert verify_numbers("총 사업비 500(백만원)", ev).ok          # 같은 금액의 단위 바꿈은 인정
    assert verify_numbers("(단위: 백만원)\n| 사업비 | 500 |", ev).ok
    assert not verify_numbers("(단위: 백만원)\n| 사업비 | 700 |", ev).ok
