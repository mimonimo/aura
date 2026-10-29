from zzaimy.verify.numbers import flag_unsupported


def test_year_month_dates_not_flagged():
    text = "연차별 기간: 2차년도(2023.3~2024.2), 3차년도(2024.3~2025.2)"
    out, flagged = flag_unsupported(text, ["총 사업기간 2022. 3. 1. ~ 2025. 2. 28."])
    assert flagged == [] and out == text


def test_real_amount_still_flagged():
    out, flagged = flag_unsupported("1인당 350,000원 지원", ["지원 기준 없음"])
    assert flagged and "(근거 확인 필요)" in out


def test_spaced_unit_declaration_in_table():
    from zzaimy.verify.numbers import mask_unsupported
    t = "<표> 예산 집행 계획 (단위 : 백만 원, %)\n| 비목명 | 예산 | 비율 |\n| 합계 | 500 | 100 |"
    out, bad = mask_unsupported(t, ["예산은 5억 원 정도"])
    assert "500" not in bad


def test_markdown_bold_dropped_from_body():
    from zzaimy.app import webhwp_write as ww
    items = ww.parse_body("가) **중요** 내용 * 비고\n| 구분 | 금액 |\n|---|---|\n| **합계** | __500__ |")
    assert items[0] == ("text", "가) 중요 내용 * 비고") and items[1][1][1] == ["합계", "500"]
