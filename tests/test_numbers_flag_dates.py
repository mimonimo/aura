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


def test_short_year_dates_not_flagged():
    from zzaimy.verify.numbers import mask_unsupported
    out, bad = mask_unsupported("선정 평가(’26.9월 초 실시, ’26.9월 말 결과 발표)", ["공고문"])
    assert bad == []


def test_plain_fence_with_arrows_is_diagram_and_without_is_text():
    from zzaimy.app import webhwp_write as ww
    items = ww.parse_body("가) 흐름\n```\n[사업 신청]\n   ↓\n[선정 평가]\n```\n나) 끝")
    kinds = [k for k, _ in items]
    assert "diagram" in kinds and not any("```" in str(v) for _, v in items)
    items2 = ww.parse_body("가) 예시\n```\n그냥 글\n```")
    assert [k for k, _ in items2] == ["text"] and "```" not in items2[0][1] and "그냥 글" in items2[0][1]
