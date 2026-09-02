"""검증기 3종 테스트 (W1-W2 TASK-08).

수치 대조 · 배점 커버리지 · 예산 계산. 전부 합성 데이터.
검증기는 결정론적이어야 한다 — LLM 판단 금지 (절대 규칙 5, 브리프 6장).
"""

from decimal import Decimal

import pytest

from zzaimy.verify.budget import BudgetItem, compute_budget
from zzaimy.verify.coverage import CoverageReport, check_coverage
from zzaimy.verify.numbers import extract_numbers, verify_numbers

# --- 1. 수치 대조: 생성 수치 ⊆ 인출 근거 ---


def test_extract_numbers_finds_korean_formats():
    text = "참여인원 1,234명, 만족도 4.2점, 예산 3억 5천만 원 중 120명이 수료"
    nums = extract_numbers(text)
    assert "1234" in nums
    assert "4.2" in nums
    assert "120" in nums


def test_verify_passes_when_all_numbers_have_evidence():
    evidence = "2023년 프로그램 참여인원 120명, 만족도 4.2점"
    draft = "본 사업은 120명이 참여했으며 만족도 4.2점을 달성했다."
    result = verify_numbers(draft, [evidence])
    assert result.ok
    assert result.violations == []


def test_verify_flags_numbers_without_evidence():
    evidence = "참여인원 120명"
    draft = "참여인원 120명, 취업률 87.5%를 달성했다."  # 87.5는 근거에 없다
    result = verify_numbers(draft, [evidence])
    assert not result.ok
    assert "87.5" in result.violations


def test_verify_ignores_structural_numbers():
    # 조항 번호·연도 등 문맥상 구조 숫자까지 잡으면 오탐 폭주 — 목록 마커는 제외
    result = verify_numbers("1. 사업 개요\n2. 추진 전략", [""])
    assert result.ok


# --- 2. 배점 커버리지 ---


def test_coverage_reports_addressed_and_missing():
    criteria = [
        {"name": "산학협력 실적", "points": 30, "keywords": ["산학협력"]},
        {"name": "취업 지원 체계", "points": 40, "keywords": ["취업"]},
        {"name": "재정 건전성", "points": 30, "keywords": ["재정", "예산"]},
    ]
    draft = "본교는 산학협력 실적이 풍부하며, 취업 지원 프로그램을 운영한다."
    report: CoverageReport = check_coverage(criteria, draft)
    assert report.covered_points == 70
    assert report.total_points == 100
    missing = [m.name for m in report.missing]
    assert missing == ["재정 건전성"]


def test_coverage_empty_draft_covers_nothing():
    criteria = [{"name": "A", "points": 50, "keywords": ["가나다"]}]
    report = check_coverage(criteria, "")
    assert report.covered_points == 0
    assert len(report.missing) == 1


# --- 3. 예산 계산 (LLM 금지 — 결정론) ---


def test_budget_totals_are_exact():
    items = [
        BudgetItem(name="노트북", unit_price_krw=1_500_000, quantity=10),
        BudgetItem(name="교재", unit_price_krw=25_000, quantity=200),
    ]
    table = compute_budget(items)
    assert table.rows[0].subtotal_krw == 15_000_000
    assert table.rows[1].subtotal_krw == 5_000_000
    assert table.total_krw == 20_000_000


def test_budget_respects_limit():
    items = [BudgetItem(name="장비", unit_price_krw=10_000_000, quantity=5)]
    table = compute_budget(items, limit_krw=40_000_000)
    assert not table.within_limit
    assert table.total_krw == 50_000_000


def test_budget_rejects_negative_values():
    with pytest.raises(ValueError):
        BudgetItem(name="이상값", unit_price_krw=-1, quantity=1)


def test_budget_ratio_uses_decimal():
    # 부동소수점 오차로 비율이 어긋나면 안 된다
    items = [BudgetItem(name="a", unit_price_krw=1, quantity=3)]
    table = compute_budget(items, limit_krw=9)
    assert table.usage_ratio == Decimal("3") / Decimal("9")


# --- 1b. 수치 대조 강화 — 한국어 단위 동치·위반 맥락 ---


def test_korean_unit_equivalence_passes():
    """'6천만 원'과 '60,000,000원'은 같은 수치 — 표기가 달라도 통과해야 한다."""
    from zzaimy.verify.numbers import verify_numbers

    result = verify_numbers(
        "총 예산은 6천만 원이며 학기당 250만 원을 지원한다.",
        ["예산 60,000,000원 규모", "학기당 2,500,000원 지원"],
    )
    assert result.ok, result.violations


def test_korean_composite_unit_value():
    """'3억 5천만'은 350,000,000 하나의 값."""
    from zzaimy.verify.numbers import canonical_values

    vals = canonical_values("사업비 3억 5천만 원")
    assert "350000000" in vals


def test_korean_unit_mismatch_still_flags():
    from zzaimy.verify.numbers import verify_numbers

    result = verify_numbers("총 예산은 7천만 원이다.", ["예산 60,000,000원"])
    assert not result.ok


def test_violation_contexts_for_ui():
    """위반 수치마다 초안 속 맥락 조각을 제공 — 담당자가 바로 찾도록."""
    from zzaimy.verify.numbers import verify_numbers

    result = verify_numbers(
        "참여율은 92.5%로 집계되었다.", ["참여인원 120명"]
    )
    assert not result.ok
    assert result.contexts and "92.5" in result.contexts[0]
    assert "참여율" in result.contexts[0]


# --- 3b. 예산 줄 검산 — 초안 속 곱셈식 결정론 재계산 (절대 규칙 5) ---


def test_budget_line_audit_catches_wrong_multiplication():
    from zzaimy.verify.budget import audit_budget_lines

    draft = (
        "생활비 장학금: 750백만 원(250만 원 × 24명 × 1개 학기)\n"  # 250만×24=6,000만 ≠ 750백만
        "운영비: 500만 원(50만 원 × 10회)\n"                        # 정확
    )
    issues = audit_budget_lines(draft)
    assert len(issues) == 1
    assert "750" in issues[0] and "6,000" in issues[0].replace("6000", "6,000")


def test_budget_line_audit_passes_correct_lines():
    from zzaimy.verify.budget import audit_budget_lines

    assert audit_budget_lines("총 6,000만 원(250만 원 × 24명)") == []
    assert audit_budget_lines("예산 개요만 있고 계산식 없음") == []
