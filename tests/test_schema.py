"""문서 계열 분류·메타데이터 스키마 테스트 (W1-W2 TASK-06).

파일명은 전부 합성이다. 실제 문서명을 쓰지 않는다.
"""

import pytest

from zzaimy.ingest.schema import (
    AccessLevel,
    AnnouncementMeta,
    DocSeries,
    ExpenditureMeta,
    ProgramReportMeta,
    ProposalMeta,
    classify_series,
)

# --- 4계열 구분 ---


def test_doc_series_has_exactly_four_members():
    assert {s.name for s in DocSeries} == {
        "ANNOUNCEMENT",
        "PROPOSAL",
        "PROGRAM_REPORT",
        "EXPENDITURE",
    }


# --- 계열 분류기 (규칙 기반, 파일명 우선) ---


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2024년_전문대학혁신지원사업_공고문.pdf", DocSeries.ANNOUNCEMENT),
        ("사업운영지침_v2.hwp", DocSeries.ANNOUNCEMENT),
        ("평가기준표_최종.pdf", DocSeries.ANNOUNCEMENT),
        ("2023_합성대사업_사업계획서.pdf", DocSeries.PROPOSAL),
        ("취업역량강화_프로그램_계획서.hwp", DocSeries.PROGRAM_REPORT),
        ("현장실습_프로그램_결과보고서.pdf", DocSeries.PROGRAM_REPORT),
        ("기자재_구매_정산내역.xlsx", DocSeries.EXPENDITURE),
        ("재료비_지출결의서.pdf", DocSeries.EXPENDITURE),
    ],
)
def test_classify_series_by_filename(filename, expected):
    assert classify_series(filename) == expected


def test_program_keyword_beats_plain_proposal():
    # "프로그램 계획서"는 ③이지 ②(사업계획서)가 아니다 — 순서 있는 규칙 확인
    assert classify_series("프로그램계획서.pdf") == DocSeries.PROGRAM_REPORT


def test_unknown_filename_returns_none():
    # 추측으로 채우지 않는다 — 분류 불가는 None으로 보고하고 사람이 정한다
    assert classify_series("회의록_0901.pdf") is None


# --- 열람 등급 (미해결 질문 #7 — 값 체계 미정, 자리 확보) ---


def test_access_level_defaults_to_most_restrictive():
    meta = ProposalMeta(doc_id="d1", source_name="합성_사업계획서.pdf")
    assert meta.access_level == AccessLevel.RESTRICTED


def test_access_level_is_string_valued_for_search_filters():
    # 벡터DB 메타데이터 필터(문자열 매칭)에 바로 쓸 수 있어야 한다
    assert all(isinstance(level.value, str) for level in AccessLevel)


# --- 계열별 메타데이터: series 필드는 고정이다 ---


@pytest.mark.parametrize(
    ("model", "series"),
    [
        (AnnouncementMeta, DocSeries.ANNOUNCEMENT),
        (ProposalMeta, DocSeries.PROPOSAL),
        (ProgramReportMeta, DocSeries.PROGRAM_REPORT),
        (ExpenditureMeta, DocSeries.EXPENDITURE),
    ],
)
def test_series_field_is_fixed_per_model(model, series):
    meta = model(doc_id="d1", source_name="합성.pdf")
    assert meta.series == series


def test_series_field_cannot_be_overridden_to_another_series():
    with pytest.raises(ValueError):
        ProposalMeta(doc_id="d1", source_name="합성.pdf", series=DocSeries.ANNOUNCEMENT)


# --- 계열별 고유 필드 골격 ---


def test_proposal_meta_tracks_selection_result():
    meta = ProposalMeta(doc_id="d1", source_name="합성.pdf", selected=True)
    assert meta.selected is True
    # 미상이면 None — 지어내지 않는다
    assert ProposalMeta(doc_id="d2", source_name="합성2.pdf").selected is None


def test_announcement_meta_has_agency_and_deadline_slots():
    meta = AnnouncementMeta(doc_id="d1", source_name="합성_공고.pdf")
    assert meta.agency is None
    assert meta.deadline is None
