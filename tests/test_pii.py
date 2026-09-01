"""PII 마스킹 파이프라인 테스트 (W1-W2 TASK-05).

★ 모든 테스트 데이터는 합성이다. 실제 개인정보를 절대 쓰지 않는다.
   주민등록번호·사업자등록번호는 체크섬만 유효하게 만든 가공 번호다.
"""

from dataclasses import asdict

import pytest

from zzaimy.ingest.pii import MaskedDocument, PiiMasker, RawDocument

# 체크섬 유효/무효 합성값 (scripts 참조 없이 알고리즘으로 생성)
VALID_RRN = "990101-1234563"
INVALID_RRN = "990101-1234567"
VALID_BRN = "123-45-67891"
INVALID_BRN = "123-45-67890"


@pytest.fixture(scope="module")
def masker() -> PiiMasker:
    return PiiMasker()


def mask_text(masker: PiiMasker, text: str) -> str:
    doc, _ = masker.mask(RawDocument(doc_id="t", text=text))
    return doc.text


# --- 주민등록번호 ---


def test_valid_rrn_is_masked(masker):
    out = mask_text(masker, f"담당자 주민등록번호는 {VALID_RRN} 입니다.")
    assert VALID_RRN not in out
    assert "[KR_RRN]" in out


def test_rrn_with_invalid_checksum_is_not_masked(masker):
    out = mask_text(masker, f"문서번호 {INVALID_RRN} 참조")
    assert INVALID_RRN in out


# --- 전화번호 ---


def test_mobile_phone_is_masked(masker):
    out = mask_text(masker, "연락처: 010-1234-5678")
    assert "010-1234-5678" not in out
    assert "[KR_PHONE]" in out


def test_landline_phone_is_masked(masker):
    out = mask_text(masker, "사무실 053-620-1234 로 문의")
    assert "053-620-1234" not in out


def test_date_is_not_masked_as_phone(masker):
    out = mask_text(masker, "제출 기한은 2026-09-01 이다.")
    assert "2026-09-01" in out


# --- 이메일 ---


def test_email_is_masked(masker):
    out = mask_text(masker, "문의: gildong@example.ac.kr 로 발송")
    assert "gildong@example.ac.kr" not in out
    assert "[EMAIL]" in out


# --- 사업자등록번호 ---


def test_valid_brn_is_masked(masker):
    out = mask_text(masker, f"사업자등록번호 {VALID_BRN} (주)합성상사")
    assert VALID_BRN not in out
    assert "[KR_BRN]" in out


def test_brn_with_invalid_checksum_is_not_masked(masker):
    out = mask_text(masker, f"관리번호 {INVALID_BRN} 항목")
    assert INVALID_BRN in out


# --- 계좌번호 (문맥 필수) ---


def test_account_number_with_context_is_masked(masker):
    out = mask_text(masker, "계좌번호: 110-123-456789 (합성은행)")
    assert "110-123-456789" not in out
    assert "[KR_BANK_ACCOUNT]" in out


def test_hyphenated_number_without_context_is_not_masked(masker):
    # 사업 관리번호 등 계좌 문맥 없는 번호는 건드리지 않는다
    out = mask_text(masker, "과제 관리코드 110-123-456789 로 등록됨")
    assert "110-123-456789" in out


# --- 성명 (라벨 문맥 기반) ---


def test_name_after_label_is_masked(masker):
    out = mask_text(masker, "성명: 홍길동, 소속: 산학협력단")
    assert "홍길동" not in out
    assert "[KR_NAME]" in out
    assert "산학협력단" in out


def test_plain_hangul_text_is_not_masked(masker):
    text = "본 사업은 교육과정 개편과 성과관리 지표 고도화를 목표로 한다."
    assert mask_text(masker, text) == text


# --- 복합 문서 ---


def test_multiple_entities_all_masked(masker):
    text = f"성명: 홍길동 / 연락처 010-1234-5678 / 주민등록번호 {VALID_RRN}"
    out = mask_text(masker, text)
    for secret in ("홍길동", "010-1234-5678", VALID_RRN):
        assert secret not in out


# --- 타입 강제: 마스킹을 거쳐야만 MaskedDocument가 나온다 ---


def test_mask_returns_masked_document_type(masker):
    doc, _ = masker.mask(RawDocument(doc_id="d1", text="내용 없음"))
    assert isinstance(doc, MaskedDocument)
    assert not isinstance(doc, RawDocument)
    assert doc.doc_id == "d1"


# --- 감사 로그: 무엇이 어디서 가려졌는지 남되, 원문은 남기지 않는다 ---


def test_audit_events_record_position_and_type(masker):
    text = f"주민등록번호 {VALID_RRN}"
    _, events = masker.mask(RawDocument(doc_id="d2", text=text))
    assert len(events) == 1
    ev = events[0]
    assert ev.doc_id == "d2"
    assert ev.entity_type == "KR_RRN"
    assert text[ev.start : ev.end] == VALID_RRN


def test_audit_events_do_not_contain_original_text(masker):
    _, events = masker.mask(RawDocument(doc_id="d3", text=f"번호 {VALID_RRN} 기재"))
    for ev in events:
        for value in asdict(ev).values():
            assert VALID_RRN not in str(value)
