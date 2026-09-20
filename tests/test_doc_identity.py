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
