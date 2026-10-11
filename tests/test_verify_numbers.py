

def test_unit_then_bare_number_is_one_value():
    """「1만 1799명」 = 11,799 — 근거와 초안의 표기가 달라도 같은 수(10/11 거짓 「근거 없음」)."""
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
