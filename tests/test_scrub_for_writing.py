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


def test_vision_pipe_table_masks_names_next_to_job_titles():
    """판독한 결재선·명단 표: 옆 칸이 직위(총장·부총장·팀원)면 이름을 가린다 — 잔여 스캔 21건의 원인."""
    import json

    from zzaimy.app.pipeline import DocumentProcessor
    from zzaimy.ingest.pii import PiiMasker, RawDocument

    masker = PiiMasker()
    mk = lambda t: masker.mask(RawDocument(doc_id="t", text=t))[0].text  # noqa: E731
    md = "| 팀원 | 김동호 | 부총장 | 김기종 | 협조자 | 이재용 |\n| 기간 | 2026 | 예산 | 240 | 비고 | 없음 |"
    chunks = DocumentProcessor._md_to_chunks(md, mk, page_no=88)
    cells = json.loads(chunks[0]["content"])["cells"]
    texts = [c[-1] for c in cells]
    assert "김동호" not in texts and "김기종" not in texts and "이재용" not in texts
    assert texts.count("[KR_NAME]") == 3 and "2026" in texts and "없음" in texts


def test_name_lists_are_masked_even_without_a_label():
    from zzaimy.ingest.pii import PiiMasker, RawDocument

    masker = PiiMasker()
    out = masker.mask(RawDocument(doc_id="t", text="참석 | 학과 교수 | 신**, 박재훈, 김장환, 박민규, 이재용 | 장소 기계학관"))[0].text
    assert "박재훈" not in out and "김장환" not in out and "이재용" not in out and "[KR_NAME]" in out and "기계학관" in out
    keep = masker.mask(RawDocument(doc_id="t", text="권역: 수도권, 충청권, 호남권, 영남권"))[0].text
    assert keep == "권역: 수도권, 충청권, 호남권, 영남권"
