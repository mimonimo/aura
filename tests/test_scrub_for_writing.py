"""문서에 써 넣는 글은 식별 번호만 가린다 — 기관·담당자 이름과 업무 연락처는 남는다."""

from zzaimy.app import access_guard as ag


def test_writing_scrub_keeps_contacts_but_hides_identity_numbers():
    text = "담당: 기획처 홍길동 (053-123-4567, hong@ync.ac.kr) 주민등록번호 900101-1234568 계좌번호 110-123-456789"
    out = ag.scrub_for_writing(text)
    assert "홍길동" in out and "053-123-4567" in out and "hong@ync.ac.kr" in out
    assert "900101-1234568" not in out
    assert "110-123-456789" not in out


def test_answer_scrub_still_hides_phone():
    assert "053-123-4567" not in ag.scrub("전화 053-123-4567")
