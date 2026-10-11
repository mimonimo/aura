

def test_unit_then_bare_number_is_one_value():
    """「1만 1799명」 = 11,799 — 근거와 초안의 표기가 달라도 같은 수(10/11 거짓 「근거 없음」)."""
    from zzaimy.verify.numbers import canonical_values, verify_numbers
    assert "11799" in canonical_values("고용 1만 1799명")
    assert verify_numbers("고용 11,799명", ["고용 1만 1799명"]).violations == []
    assert "20000" in canonical_values("2만 명과 3명") and "20003" not in canonical_values("2만 명과 3명")
