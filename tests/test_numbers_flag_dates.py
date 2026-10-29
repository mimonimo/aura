from zzaimy.verify.numbers import flag_unsupported


def test_year_month_dates_not_flagged():
    text = "연차별 기간: 2차년도(2023.3~2024.2), 3차년도(2024.3~2025.2)"
    out, flagged = flag_unsupported(text, ["총 사업기간 2022. 3. 1. ~ 2025. 2. 28."])
    assert flagged == [] and out == text


def test_real_amount_still_flagged():
    out, flagged = flag_unsupported("1인당 350,000원 지원", ["지원 기준 없음"])
    assert flagged and "(근거 확인 필요)" in out
