"""문서가 들어올 때 갈래와 영역을 스스로 정한다.

담당자가 문서마다 고르지 않아도 되게 하되, 확신이 없으면 고르지 않는다.
데이터는 전부 합성이다.
"""

from fastapi.testclient import TestClient

from zzaimy.app import doc_routing as R
from zzaimy.app.db import Database
from zzaimy.app.main import create_app
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder


def test_articles_mean_it_is_a_regulation():
    body = "제1조(목적) 이 규정은. 제2조(적용) 전 부서에. 제3조(해석) 총장이 정한다."
    assert R.guess_doc_type(body)[0] == "regulation"


def test_announcement_needs_program_and_conditions():
    ident = {"program": "학자금 지원", "period": "2026년 9월", "scale": "5천만 원"}
    assert R.guess_doc_type("지원 공고입니다.", ident)[0] == "grant"
    # 사업 이름만 있고 조건이 없으면 단정하지 않는다
    assert R.guess_doc_type("지원 공고입니다.", {"program": "학자금 지원"})[0] == "auto"


def test_scanned_documents_are_extraction_documents():
    assert R.guess_doc_type("아무 글", scanned=True)[0] == "ocr"


def test_plain_text_is_left_alone():
    kind, why = R.guess_doc_type("안내드립니다. 문의는 담당자에게 하십시오.")
    assert kind == "auto" and why


def _seeded(tmp_path, rows):
    db = Database(tmp_path / "t.db")
    for name, sector, body in rows:
        did = db.add_document(name, "/x", "regulation", sector=sector)
        db.replace_doc_chunks(did, [{"kind": "text", "page_no": 1, "content": body}])
    return db


def test_sector_is_learned_from_existing_documents(tmp_path):
    db = _seeded(tmp_path, [
        ("2025학년도 입학전형 시행계획.pdf", "admission", "입학 전형 일정과 모집 단위를 정한다."),
        ("2024학년도 입학전형 시행계획.pdf", "admission", "입학 전형 일정과 모집 단위를 정한다."),
        ("2023학년도 입학자 편성표.pdf", "admission", "입학자 연계교육과정 편성표이다."),
        ("계약직원 채용 공고.pdf", "recruit", "채용 분야와 임용 절차를 정한다."),
        ("교직원 채용 절차 안내.pdf", "recruit", "채용 절차와 임용 서류를 안내한다."),
        ("직원 임용 규정.pdf", "recruit", "임용과 채용에 관한 사항을 정한다."),
        ("국고보조금 정산 지침.pdf", "grant", "국고보조사업 정산과 집행을 정한다."),
        ("국고사업 기본계획.pdf", "grant", "국고사업 추진과 집행 계획이다."),
        ("사업비 집행 계획.pdf", "grant", "사업비 집행과 정산 절차이다."),
    ])
    weights = R.learn_sectors(db)
    assert set(weights) == {"admission", "recruit", "grant"}
    got, why = R.guess_sector(db, "2026학년도 입학전형 시행계획.pdf", weights)
    assert got == "admission" and why


def test_sector_abstains_when_nothing_matches(tmp_path):
    db = _seeded(tmp_path, [
        ("입학전형 계획.pdf", "admission", "입학 전형이다."),
        ("입학자 편성표.pdf", "admission", "입학자 편성표이다."),
        ("채용 공고.pdf", "recruit", "채용 절차이다."),
        ("임용 규정.pdf", "recruit", "임용 절차이다."),
        ("정산 지침.pdf", "grant", "정산 절차이다."),
        ("집행 계획.pdf", "grant", "집행 계획이다."),
        ("기본계획.pdf", "grant", "추진 계획이다."),
        ("전형 안내.pdf", "admission", "전형 안내이다."),
    ])
    got, why = R.guess_sector(db, "zzzz9999.bin", R.learn_sectors(db))
    assert got == "" and why


def test_sector_abstains_when_too_few_documents(tmp_path):
    db = _seeded(tmp_path, [("가.pdf", "grant", "국고사업이다.")])
    assert R.learn_sectors(db) == {}
    assert R.guess_sector(db, "나.pdf")[0] == ""


def test_agent_can_reclassify_a_document(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder())
    c = TestClient(app)
    c.post("/upload", files={"file": ("규정.pdf", b"%PDF fake", "application/pdf")})
    db = Database(tmp_path / "t.db")
    db.replace_doc_chunks(1, [{"kind": "text", "page_no": 1,
                               "content": "제1조(목적) 정한다. 제2조(적용) 전 부서. 제3조(해석) 총장."}])
    body = c.post("/chat/ask", data={"question": "이 문서 갈래 다시 정해줘",
                                     "page": "/doc/1"}).json()
    assert body["done"]
    assert db.get_document(1)["doc_type"] == "regulation"


# ---- 말한 대로 초안 고치기 ----

def test_agent_revises_a_draft_from_plain_words(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder())
    c = TestClient(app)
    c.post("/upload", data={"doc_type": "grant"},
           files={"file": ("공고.pdf", b"%PDF fake", "application/pdf")})
    db = Database(tmp_path / "t.db")
    db.update_document(1, draft="처음 만든 초안입니다.")

    body = c.post("/chat/ask", data={"question": "예산 부분을 더 자세히 고쳐줘",
                                     "page": "/doc/1"}).json()
    assert body["done"] and any("고쳤습니다" in line for line in body["done"])
    opinions = [r["opinion"] for r in db.get_reviews(1)]
    assert any("예산" in o for o in opinions)      # 말한 문장이 요청으로 남는다


def test_revision_needs_a_draft_first(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(),
                     responder=FakeResponder())
    c = TestClient(app)
    c.post("/upload", data={"doc_type": "grant"},
           files={"file": ("공고.pdf", b"%PDF fake", "application/pdf")})
    Database(tmp_path / "t.db").update_document(1, draft=None)
    body = c.post("/chat/ask", data={"question": "예산 부분 고쳐줘",
                                     "page": "/doc/1"}).json()
    assert any("초안이 없습니다" in line for line in body["done"])


def test_kind_comes_from_title_words_then_body_shape():
    from zzaimy.app.doc_routing import guess_kind

    assert guess_kind("(붙임2) 첨단분야 혁신융합대학 사업 가 신청서.hwpx", "")[0] == "form"
    assert guess_kind("복학원", "")[0] == "form"
    assert guess_kind("2025학년도 1학기 학생 건의 사항 조치 보고서", "")[0] == "report"
    assert guess_kind("kcue_교원공채_심사기준표.pdf", "")[0] == "criteria"
    assert guess_kind("대학원 학칙", "")[0] == "regulation"
    assert guess_kind("사업 계획 및 업무처리기준", "")[0] == "guideline"
    assert guess_kind("2026년 지방대학 육성사업 공고.hwpx", "")[0] == "announcement"
    form_body = "성명 | 년 월 일 | ☐ 복학 ☐ 조기복학 | 학번 ○○○ | 입대일자 년 월 일 | 전역일자 년 월 일"
    assert guess_kind("이름없음.pdf", form_body)[0] == "form"
    ann = "2026년 지역혁신 사업 공고\n교육부는 다음과 같이 공고합니다.\n신청 기간: 3. 2. ~ 3. 20."
    assert guess_kind("law01.pdf", ann)[0] == "announcement"
    assert guess_kind("law01.pdf", "제1조(목적) 이 규정은 … 제2조(정의) … 제3조 … 제4조 … 제5조 … 제6조 …")[0] == "regulation"
    assert guess_kind("law01.pdf", "그냥 평범한 글입니다.")[0] == ""


def test_route_reports_kind_alongside_type_and_sector(tmp_path):
    from zzaimy.app.db import Database
    from zzaimy.app.doc_routing import route

    r = route(Database(tmp_path / "t.db"), "복학원", "성명 | 년 월 일 | ☐ 복학")
    assert r["kind"] == "form" and r["why_kind"]
