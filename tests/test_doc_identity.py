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
    assert "문서 정체" in page
    assert "통영시 학자금 지원" in page


def test_identity_route_rejects_missing_document(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder())
    r = TestClient(app).post("/doc/999/identity", follow_redirects=False)
    assert r.status_code == 404
