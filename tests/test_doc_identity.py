"""문서 정체 파악 — 본문에서 인출하고, 본문에 없는 값은 버린다.

절대규칙 1(수치는 인출하고 생성하지 않는다)을 코드가 지키는지 확인한다.
데이터는 전부 합성이며 모델은 가짜 호출로 대체한다.
"""

from fastapi.testclient import TestClient

from zzaimy.app import doc_identity as di
from zzaimy.app.db import Database
from zzaimy.app.main import create_app
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder

BODY = (
    "2026년 통영시 대학생 학자금 대출이자 지원 공고\n"
    "통영시장이 공고합니다. 지원 대상은 통영시에 1년 이상 거주한 대학생입니다.\n"
    "지원 규모는 총 5천만 원입니다. 신청 기간은 2026년 9월 2일부터입니다."
)


def test_keeps_only_values_present_in_body():
    found = {
        "program": "통영시 대학생 학자금 대출이자 지원",
        "organizer": "통영시장",
        "year": "2026년",
        "scale": "총 5천만 원",
        "law": "지방재정법",              # 본문에 없다
        "period": "2026년 10월 1일부터",   # 날짜가 본문과 다르다
    }
    kept, dropped = di.verify(found, BODY)
    assert set(kept) == {"program", "organizer", "year", "scale"}
    assert any("law" in d for d in dropped)
    assert any("period" in d for d in dropped)


def test_ignores_spacing_and_bracket_differences():
    kept, _ = di.verify({"organizer": "통영 시장"}, BODY)
    assert kept["organizer"] == "통영 시장"      # 띄어쓰기만 다르면 같은 값으로 본다


def test_extract_reads_json_from_fenced_answer():
    answer = '```json\n{"program": "통영시 대학생 학자금 대출이자 지원", "year": "2026년"}\n```'
    r = di.extract(BODY, lambda prompt: answer)
    assert r["ok"] is True
    assert r["identity"]["program"].startswith("통영시")


def test_extract_reports_when_model_answers_nothing_usable():
    r = di.extract(BODY, lambda prompt: "잘 모르겠습니다.")
    assert r["ok"] is False and "JSON" in r["error"]


def test_extract_reports_model_failure():
    def boom(prompt):
        raise ConnectionError("서버 없음")

    r = di.extract(BODY, boom)
    assert r["ok"] is False and "실패" in r["error"]


def test_signature_matches_across_spacing():
    a = di.signature({"program": "통영시 학자금 지원"})
    b = di.signature({"program": "통영시  학자금  지원"})
    assert a and a == b


def test_signature_empty_when_no_program():
    assert di.signature({"year": "2026년"}) == ""


def test_same_program_documents_link(tmp_path):
    db = Database(tmp_path / "t.db")
    a = db.add_document("2025공고.pdf", "/x", "regulation")
    b = db.add_document("2026공고.pdf", "/y", "regulation")
    c = db.add_document("무관문서.pdf", "/z", "regulation")
    db.set_doc_identity(a, {"program": "통영시 학자금 지원", "year": "2025년"})
    db.set_doc_identity(b, {"program": "통영시 학자금 지원", "year": "2026년"})
    db.set_doc_identity(c, {"program": "전혀 다른 사업", "year": "2026년"})

    linked = db.docs_sharing_program(a)
    assert [d["id"] for d in linked] == [b]          # 같은 사업만 잇는다
    assert db.docs_sharing_program(c) == []


def test_document_page_shows_identity(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder())
    c = TestClient(app)
    c.post("/upload", files={"file": ("공고.pdf", b"%PDF fake", "application/pdf")})
    db = Database(tmp_path / "t.db")
    db.set_doc_identity(1, {"program": "통영시 학자금 지원", "organizer": "통영시장"})
    page = c.get("/doc/1").text
    assert 'class="doc-meta"' in page
    assert "통영시 학자금 지원" in page


def test_identity_route_rejects_missing_document(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder())
    r = TestClient(app).post("/doc/999/identity", follow_redirects=False)
    assert r.status_code == 404


def test_title_is_read_from_the_head_of_regulation_text():
    """'law03.pdf'처럼 뜻 없는 파일 이름 대신 첫 쪽의 규정 이름과 날짜를 쓴다."""
    from zzaimy.app.doc_title import display_name, find_title

    head = ("10-03-1\n영남이공대학교 산학협력단 사무분장 규정\n"
            "학과장회 통과일자 : 2022년 05월 26일\n제 1 장 총 칙\n제 1 조(목적) 이 규정은 …")
    assert find_title(head) == ("영남이공대학교 산학협력단 사무분장 규정", "2022년 05월 26일")
    doc = {"filename": "law03.pdf", "stored_path": "", "masked_text": head}
    assert display_name(doc) == "영남이공대학교 산학협력단 사무분장 규정 · 2022년 05월 26일"


def test_meaningful_filename_is_not_replaced_by_a_partial_title():
    """제목이 두 줄로 나뉘어 뒷줄만 잡혔으면 멀쩡한 파일 이름을 그대로 둔다."""
    from zzaimy.app.doc_title import display_name

    doc = {"filename": "대학재정지원사업_운영관리_매뉴얼.pdf", "stored_path": "",
           "masked_text": "대학재정지원사업\n공동 운영ㆍ관리 매뉴얼\n2024. 3."}
    assert display_name(doc) == "대학재정지원사업_운영관리_매뉴얼.pdf"


def test_reading_identity_again_keeps_title_and_date(tmp_path):
    """모델로 사업 정보를 다시 읽어도 첫 쪽에서 찾은 이름·날짜는 남는다."""
    db = Database(tmp_path / "t.db")
    d = db.add_document(filename="law03.pdf", stored_path="", doc_type="regulation")
    import json
    with db._conn() as conn:
        conn.execute("UPDATE documents SET identity = ? WHERE id = ?",
                     (json.dumps({"title": "사무분장 규정", "date": "2022년 05월 26일"}), d))
    db.set_doc_identity(d, {"organizer": "산학협력단"})
    got = db.get_doc_identity(d)
    assert got["title"] == "사무분장 규정" and got["date"] == "2022년 05월 26일"
    assert got["organizer"] == "산학협력단"


def test_meaningless_filename_gets_first_title_line():
    from zzaimy.app.doc_title import display_name

    doc = {"filename": "www.ync.ac.kr__UPLOAD_PDF_CommonFile_2023_11_HbY1XWA0.PDF", "stored_path": "",
           "masked_text": "2023-11-02\n2024학년도 1학기 국가근로장학금 신청 안내\n1. 신청 기간: 2023.11.20.~12.5.\n학생 여러분의 많은 참여 바랍니다."}
    assert display_name(doc) == "2024학년도 1학기 국가근로장학금 신청 안내"
    # 한글 파일 이름이 있으면 느슨한 규칙을 쓰지 않는다
    doc2 = dict(doc, filename="장학금_안내.pdf")
    assert display_name(doc2) == "장학금_안내.pdf"


def test_loose_title_edge_cases():
    from zzaimy.app.doc_title import loose_title

    assert loose_title("입 찰 공 고\n1. 입찰에 부치는 사항") == "입찰공고"
    assert loose_title("2025학년도 입학자\n연계교육과정 편성표\n학과: 건축과") == "2025학년도 입학자 연계교육과정 편성표"
    assert loose_title("학과명 발생일 건의사항 학과 답변 내용 조치 내용 증빙서류 완료일 계획\n2024학년도 학생 건의 사항 조치 보고서") \
        == "2024학년도 학생 건의 사항 조치 보고서"


def test_title_comes_from_the_document_not_the_filename(tmp_path):
    """파일 이름이 test.pdf 여도(같은 이름이 여럿이어도) 문서 안의 제목으로 구분된다."""
    from zzaimy.app.db import Database
    from zzaimy.app.doc_title import display_name

    db = Database(tmp_path / "t.db")
    body = ("영남이공대학교 산학협력단 운영 규정\n제정 2022년 05월 26일\n\n"
            "제1조(목적) 이 규정은 산학협력단의 운영에 관한 사항을 정함을 목적으로 한다.")
    ids = []
    for _ in range(2):
        doc_id = db.add_document(filename="test.pdf", stored_path=str(tmp_path / "test.pdf"),
                                 doc_type="regulation")
        db.rename_from_text(doc_id, body)          # 반입 단계에서 부르는 그 함수
        db.update_document(doc_id, status="reviewed", masked_text=body)
        ids.append(doc_id)
    for doc_id in ids:
        doc = db.get_document(doc_id)
        ident = db.get_doc_identity(doc_id)
        assert ident["title"] == "영남이공대학교 산학협력단 운영 규정"
        # 문서 이름 자체가 바뀐다 — 화면마다 따로 계산하지 않아도 된다
        assert doc["filename"].startswith("영남이공대학교 산학협력단 운영 규정")
        assert ident["original_filename"] == "test.pdf"     # 올라온 파일 이름은 남는다
        assert doc["stored_path"].endswith("test.pdf")      # 파일 자체는 그대로
        # 같은 제목이 둘이면 뒤에 구분되는 말이 붙으므로 시작만 같다
        assert doc["filename"].startswith(display_name(doc))


def test_same_title_documents_get_distinguishable_names(tmp_path):
    """제목이 같은 문서가 여럿 들어오면 본문에서 구분되는 말을 붙인다."""
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    bodies = [
        "2022학년도 입학자\n연계교육과정 편성표\n학과(계열) ICT반도체전자계열\n연계편입 전자공학전공",
        "2022학년도 입학자\n연계교육과정 편성표\n학과(계열) 스마트융합기계계열\n연계편입 기계공학전공",
    ]
    names = []
    for body in bodies:
        doc_id = db.add_document(filename="down.pdf", stored_path=str(tmp_path / "down.pdf"),
                                 doc_type="auto")
        db.rename_from_text(doc_id, body)
        names.append((db.get_document(doc_id) or {})["filename"])
    assert names[0] != names[1]                       # 서로 구분된다
    assert all(n.startswith("2022학년도 입학자 연계교육과정 편성표") for n in names)
    assert "스마트융합기계계열" in names[1] and "|" not in names[1]   # 표 구분자 없이 값만


def test_table_rows_do_not_become_titles():
    """서식 문서의 첫 쪽은 표다 — 항목 이름이 늘어선 줄은 제목이 아니다."""
    from zzaimy.app.doc_title import loose_title

    assert loose_title("|학년|학년|학년||||학번|학번|학번||||") is None
    assert loose_title("비고|비고||||||||") is None
    assert loose_title("학과(계열) | | 수험번호 |") is None
    # 문서 종류로 끝나는 칸은 그 문서의 이름이다
    assert loose_title("영문성명등록신청서 | | 전결 | | |") == "영문성명등록신청서"
    assert loose_title("[]행사계획서|결재|||") == "행사계획서"
    # 표 줄 다음에 제목 줄이 오면 그 줄을 쓴다
    assert loose_title("|학년|학번|\n2024학년도 학생 건의 사항 조치 보고서") \
        == "2024학년도 학생 건의 사항 조치 보고서"


def test_document_number_and_item_marker_lines_are_not_titles():
    """공고는 첫 줄이 문서 번호다 — 제목은 그다음 줄에 있다."""
    from zzaimy.app.doc_title import loose_title

    assert loose_title("통영시공고 제2026-1669호\n2026년 상반기분 통영시 대학생 학자금 이자 지원 공고") \
        == "2026년 상반기분 통영시 대학생 학자금 이자 지원 공고"
    assert loose_title("가. 신청기간:2026. 9. 2.(수)\n1.대학(원) 재학 증명서") is None
    # 값만 남은 줄은 건너뛰고 다음 줄을 본다
    assert loose_title(": 대구광역시 중구 북성로 107 번지\n응시원서") == "응시원서"
    # 표의 번호 칸('9납입금 내역')도 제목이 아니다. 기간 표기('2026년')는 제목이다
    assert loose_title("9납입금 내역\n등록금 |  | 3,393,000") is None
    assert loose_title("2026년 상반기분 통영시 대학생 학자금 이자 지원 공고") \
        == "2026년 상반기분 통영시 대학생 학자금 이자 지원 공고"


def test_attachment_markers_and_symbols_are_not_part_of_the_name():
    """'붙임1.'·'[붙임2]'·'(서식2-1)' 은 문서가 실린 자리다. ★·이모지·역슬래시·URL 구분자도 뗀다."""
    from zzaimy.app.doc_title import tidy_name

    assert tidy_name("붙임1. 2024년 첨단분야 혁신융합대학 사업 추진계획.hwpx") \
        == "2024년 첨단분야 혁신융합대학 사업 추진계획.hwpx"
    assert tidy_name("[붙임2]+2025+글로컬대학+지정계획(결재본)★.pdf") == "2025 글로컬대학 지정계획(결재본).pdf"
    assert tidy_name("(서식2-1) 2026년 지방대학 특성화 선도대학 육성사업 사업계획서 서식(단독형)") \
        == "2026년 지방대학 특성화 선도대학 육성사업 사업계획서 서식(단독형)"
    assert tidy_name("지원범위:2026년 상반기(2026.1.1.\\~6.30.까지)발생이자") \
        == "지원범위:2026년 상반기(2026.1.1.~6.30.까지)발생이자"
    assert tidy_name("🎉 2026 대한민국 SNS 대상 투표 이벤트 ✨") == "2026 대한민국 SNS 대상 투표 이벤트"
    assert tidy_name("대학재정지원사업_운영관리_매뉴얼.pdf") == "대학재정지원사업_운영관리_매뉴얼.pdf"


def test_field_value_lines_are_not_titles():
    from zzaimy.app.doc_title import loose_title

    assert loose_title("지원범위:2026년 상반기(2026.1.1.~6.30.까지)발생이자\n가. 대상") is None


def test_model_title_is_accepted_only_when_it_is_in_the_body():
    """모델은 제목 줄을 가리킬 뿐이다 — 본문에 없는 말은 받지 않는다."""
    from zzaimy.app.doc_identity import find_title_by_model

    body = "한국장학재단\n2026년 2학기 고졸 후학습자 장학사업 신규장학생 신청 안내\n1. 신청 기간: 2026. 8. 1. ~ 8. 31."
    assert find_title_by_model(body, lambda p: "2026년 2학기 고졸 후학습자 장학사업 신규장학생 신청 안내") \
        == "2026년 2학기 고졸 후학습자 장학사업 신규장학생 신청 안내"
    assert find_title_by_model(body, lambda p: "고졸 후학습자를 위한 장학금 안내문") is None   # 지어낸 제목
    assert find_title_by_model(body, lambda p: "없음") is None
    assert find_title_by_model(body, lambda p: "안내") is None                            # 너무 짧다


def test_meaningless_url_filename_uses_model_title(tmp_path):
    from zzaimy.app.db import Database
    from zzaimy.app.doc_title import meaningless_filename

    assert meaningless_filename("www.ync.ac.kr_kor_ajx_json_UploadMgr_downloadRun.do_qcode_Qm9hcmQsNTE5MzgsWQ.hwp")
    assert not meaningless_filename("rise2026_seoul_공고.pdf")
    db = Database(tmp_path / "t.db")
    body = "한국장학재단\n지원범위:2026년 상반기 발생이자\n2026년 대학생 학자금대출 부담경감 지원 신청 안내\n가. 대상: 재학생"
    doc_id = db.add_document(filename="www.ync.ac.kr_kor_ajx_json_UploadMgr_downloadRun.do_qcode_Qm9hcmQsNTE5MzgsWQ.hwp",
                             stored_path="", doc_type="regulation")
    db.rename_from_text(doc_id, body, ask=lambda p: "2026년 대학생 학자금대출 부담경감 지원 신청 안내")
    assert db.get_document(doc_id)["filename"] == "2026년 대학생 학자금대출 부담경감 지원 신청 안내"


def test_same_title_documents_are_told_apart_by_subtitle(tmp_path):
    """제목이 같은 매뉴얼 둘 — 제목 다음 줄(부제)이 가른다. 접수번호는 마지막 수단이다."""
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    bodies = ["가구원 정보제공 동의 절차\n( 홈페이지 , 모바일앱 )\n한국장학재단 국가장학실",
              "가구원 정보제공 동의 절차\n( 웰로'Wello' 앱 사용 매뉴얼 )\n한국장학재단 국가장학실"]
    names = []
    for body in bodies:
        doc_id = db.add_document(filename="www.ync.ac.kr_qcode_Qm9hcmQsNTE5MzgsWQ.pdf", stored_path="",
                                 doc_type="regulation")
        db.rename_from_text(doc_id, body)
        names.append(db.get_document(doc_id)["filename"])
    assert names[0] == "가구원 정보제공 동의 절차"
    assert names[1] == "가구원 정보제공 동의 절차 · 웰로'Wello' 앱 사용 매뉴얼"


def test_renaming_updates_the_citation_title(tmp_path):
    """이름이 바뀌면 인용에 쓰는 기준명도 같이 바뀐다."""
    from zzaimy.app.db import Database
    from zzaimy.app.regulations import chunk_document

    db = Database(tmp_path / "t.db")
    body = "붙임1. 2024년 글로컬대학 지정계획\n제1조(목적) 이 계획은 글로컬대학 지정에 관한 사항을 정한다."
    doc_id = db.add_document(filename="붙임1. 2024년 글로컬대학 지정계획.hwpx", stored_path="", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "붙임1. 2024년 글로컬대학 지정계획.hwpx", chunk_document(body))
    db.rename_from_text(doc_id, body, overwrite=True)
    assert db.get_document(doc_id)["filename"] == "2024년 글로컬대학 지정계획.hwpx"
    assert {c["reg_title"] for c in db.list_regulation_chunks()} == {"2024년 글로컬대학 지정계획.hwpx"}


def test_same_file_is_not_ingested_twice(tmp_path):
    from zzaimy.app.db import Database
    from zzaimy.app.pipeline import DocumentProcessor

    f1 = tmp_path / "a.txt"; f1.write_text("영남이공대학교 산학협력단 운영 규정\n제1조(목적) 운영에 관한 사항.")
    f2 = tmp_path / "b.txt"; f2.write_bytes(f1.read_bytes())
    db = Database(tmp_path / "t.db")
    a = db.add_document(filename="a.txt", stored_path=str(f1), doc_type="regulation")
    b = db.add_document(filename="b.txt", stored_path=str(f2), doc_type="regulation")
    import hashlib
    db.record_content_hash(a, hashlib.sha256(f1.read_bytes()).hexdigest())
    db.update_document(a, status="reviewed")
    DocumentProcessor().process(db, b, f2)
    doc = db.get_document(b)
    assert doc["status"] == "failed" and "같은 내용" in doc["error"] and f"#{a}" in doc["error"]
