"""외부 참조 이그레스 게이트웨이 — 세척·분류의 안전 계약 테스트.

핵심 불변식: 내부 정보(학교명·인명·개인정보)는 세척을 통과해도 남지 않는다.
확신하지 못하는 질의는 자동 전송되지 않는다(safe가 아니다).
"""

from __future__ import annotations

from zzaimy.app.egress import Verdict, classify, scrub


def test_scrub_removes_school_name():
    """학교명·기관 식별자는 세척 후 텍스트에 남지 않는다."""
    for name in ["영남이공대학교", "영남이공대", "YNC", "짜이미", "ZZAIMY"]:
        out = scrub(f"{name}의 2026년 국고사업 절차를 알려줘")
        assert name not in out.text, f"{name} 이(가) 세척 후 남음"


def test_scrub_removes_pii():
    """전화번호·주민번호 등 개인정보는 세척 후 남지 않는다."""
    src = "담당자 홍길동 010-1234-5678, 주민번호 900101-1234567 문의"
    out = scrub(src)
    assert "010-1234-5678" not in out.text
    assert "900101-1234567" not in out.text
    assert "홍길동" not in out.text


def test_scrub_reports_what_was_removed():
    """무엇을 지웠는지 감사용으로 보고한다(빈 세척은 removed가 비어야)."""
    out = scrub("영남이공대학교 국고사업")
    assert out.removed, "제거 내역이 비어 있으면 안 됨"
    clean = scrub("국고 보조사업의 일반적인 예산 편성 절차는?")
    assert not clean.removed


def test_generic_query_is_safe():
    """내부 정보가 전혀 없는 일반 질의는 safe(자동 전송 가능)."""
    out = scrub("국고 보조사업 계획서의 일반적인 목차 구성은 무엇인가?")
    assert classify(out) is Verdict.SAFE


def test_school_name_query_is_never_safe():
    """학교명이 든 질의는 세척해도 자동 전송(safe)되지 않는다 — fail-closed."""
    out = scrub("영남이공대학교가 2026년에 받은 국고사업 목록 알려줘")
    assert classify(out) is not Verdict.SAFE


def test_residual_proper_noun_needs_review_not_send():
    """세척이 못 잡은 미상 고유명사가 남으면 최소 review — 그냥 safe로 보내지 않는다."""
    out = scrub("한빛제일고등학교 특별전형 요강을 알려줘")
    assert classify(out) in (Verdict.REVIEW, Verdict.BLOCKED)
