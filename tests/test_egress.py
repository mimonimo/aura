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


# ---------------------------------------------------------------------------
# 게이트웨이 오케스트레이션 — 제출·기록·승인·전송 상태 흐름
# ---------------------------------------------------------------------------

import json

import pytest

from zzaimy.app import egress
from zzaimy.app.db import Database


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "egress.db")


@pytest.fixture(autouse=True)
def _external_disabled(monkeypatch):
    """기본은 외부 전송 비활성 — 실제 네트워크로 나가는 테스트는 없다."""
    monkeypatch.delenv("ZZAIMY_EXTERNAL_ENABLED", raising=False)
    monkeypatch.delenv("ZZAIMY_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_submit_safe_is_held_when_external_disabled(db):
    """safe 판정이라도 전송 비활성이면 held(전송 대기)로 남고 기록된다."""
    row = egress.submit(db, "국고 보조사업의 일반적인 정산 절차는?", "zzdev")
    assert row["verdict"] == "safe"
    assert row["status"] == "held"
    assert "전송 보류" in (row["error"] or "")
    assert row["original"] and row["scrubbed"]


def test_submit_internal_query_is_queued(db):
    """내부 기관이 든 질의는 승인 대기 큐로 — 자동 전송되지 않는다."""
    row = egress.submit(db, "영남이공대학교의 국고사업 절차를 알려줘", "zzaimy", source="chat")
    assert row["verdict"] == "review"
    assert row["status"] == "queued"
    assert "영남이공대" not in row["scrubbed"]
    removed = json.loads(row["removed"])
    assert any(r.startswith("ORG:") for r in removed)


def test_approve_moves_to_approved_and_holds_offline(db):
    """승인하면 approved — 전송 비활성이라 나가지는 않고 승인자·시각이 남는다."""
    row = egress.submit(db, "영남이공대학교 관련 일반 절차", "zzaimy")
    out = egress.decide(db, row["id"], approve=True, decided_by="zzdev")
    assert out["status"] == "approved"
    assert out["decided_by"] == "zzdev"
    assert out["decided_at"]


def test_deny_is_terminal(db):
    row = egress.submit(db, "영남이공대학교 관련 일반 절차", "zzaimy")
    out = egress.decide(db, row["id"], approve=False, decided_by="zzdev")
    assert out["status"] == "denied"
    with pytest.raises(ValueError):
        egress.decide(db, row["id"], approve=True, decided_by="zzdev")


def test_decide_rejects_non_queued(db):
    row = egress.submit(db, "일반적인 예산 편성 절차는?", "zzdev")  # safe → held
    with pytest.raises(ValueError):
        egress.decide(db, row["id"], approve=True, decided_by="zzdev")


def test_masker_failure_is_blocked(db, monkeypatch):
    """마스커를 못 쓰면 세척을 보증 못 하므로 무조건 차단 — fail-closed."""
    def broken():
        raise RuntimeError("마스커 로드 실패")

    monkeypatch.setattr(egress, "_get_masker", broken)
    row = egress.submit(db, "아무 질의", "zzaimy")
    assert row["verdict"] == "blocked"
    assert row["status"] == "blocked"


def test_send_path_when_enabled(db, monkeypatch):
    """전송 활성 시 safe 질의는 즉시 전송되고 응답·시각이 기록된다."""
    monkeypatch.setattr(egress, "external_status", lambda: (True, ""))
    monkeypatch.setattr(egress, "_send_external", lambda text: "일반 지식 답변")
    row = egress.submit(db, "국고 보조사업의 일반적인 목차는?", "zzdev")
    assert row["status"] == "answered"
    assert row["response"] == "일반 지식 답변"
    assert row["sent_at"]


def test_send_failure_is_recorded_and_retryable(db, monkeypatch):
    monkeypatch.setattr(egress, "external_status", lambda: (True, ""))

    def boom(text):
        raise RuntimeError("연결 실패")

    monkeypatch.setattr(egress, "_send_external", boom)
    row = egress.submit(db, "국고 보조사업의 일반적인 목차는?", "zzdev")
    assert row["status"] == "failed"
    assert "연결 실패" in row["error"]

    monkeypatch.setattr(egress, "_send_external", lambda text: "복구 후 답변")
    out = egress.retry_send(db, row["id"])
    assert out["status"] == "answered"
    assert out["response"] == "복구 후 답변"


def test_stats_counts_by_status(db):
    egress.submit(db, "일반 절차 질문 하나", "zzdev")            # held
    egress.submit(db, "영남이공대학교 관련 질문", "zzaimy")       # queued
    stats = db.egress_stats()
    assert stats["total"] == 2
    assert stats.get("held") == 1
    assert stats.get("queued") == 1
