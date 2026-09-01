"""문서 접수·검토 플랫폼 테스트 (대시보드 v0.1).

LLM·파서 등 무거운 단계는 가짜 프로세서를 주입해 API·DB 계층만 검증한다.
데이터는 전부 합성.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zzaimy.app.db import Database
from zzaimy.app.main import create_app


class FakeProcessor:
    """실제 파이프라인(파싱→마스킹→검토의견) 대신 즉시 완료 처리."""

    def process(self, db: Database, doc_id: int, file_path: Path) -> None:
        db.update_document(
            doc_id,
            status="reviewed",
            masked_text="합성 마스킹 본문",
            series="proposal",
            ai_review="합성 검토 의견: 형식 적합.",
        )

    def reprocess(self, db: Database, doc_id: int) -> None:
        db.update_document(
            doc_id, status="reviewed", ai_review="재검토 의견: 담당자 요청 반영."
        )

    def extract_text(self, file_path: Path) -> str:
        return "합성 첨부 본문"


class FakeDrafter:
    """실제 초안 생성(스키마→섹션 생성→검증) 대신 즉시 완료 처리."""

    def generate(self, db: Database, doc_id: int) -> None:
        db.update_document(
            doc_id,
            draft="## 합성 초안 섹션\n합성 초안 본문이다.",
            coverage="배점 커버리지 70/100점",
        )


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "문서 접수" in r.text


def test_upload_creates_document_and_processes(client):
    r = client.post(
        "/upload",
        files={"file": ("합성문서.pdf", b"%PDF-1.4 fake", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303  # 목록으로 리다이렉트
    r = client.get("/")
    assert "합성문서.pdf" in r.text


def test_document_detail_shows_ai_review(client):
    client.post("/upload", files={"file": ("a.pdf", b"%PDF fake", "application/pdf")})
    r = client.get("/doc/1")
    assert r.status_code == 200
    assert "합성 검토 의견" in r.text


def test_reviewer_opinion_is_saved(client):
    client.post("/upload", files={"file": ("a.pdf", b"%PDF fake", "application/pdf")})
    r = client.post("/doc/1/review", data={"opinion": "검토자 의견: 보완 필요"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/doc/1")
    assert "보완 필요" in r.text


def test_unknown_document_returns_404(client):
    assert client.get("/doc/999").status_code == 404


def test_upload_rejects_disallowed_extension(client):
    r = client.post(
        "/upload",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_upload_stores_doc_type(client):
    client.post(
        "/upload",
        data={"doc_type": "recruit"},
        files={"file": ("이력서_합성.pdf", b"%PDF fake", "application/pdf")},
    )
    r = client.get("/doc/1")
    assert "채용" in r.text  # 문서 유형 라벨 표시


def test_draft_generation_flow(client):
    client.post(
        "/upload",
        data={"doc_type": "grant"},
        files={"file": ("공고_합성.pdf", b"%PDF fake", "application/pdf")},
    )
    r = client.post("/doc/1/draft", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/doc/1")
    assert "합성 초안 섹션" in r.text
    assert "배점 커버리지 70/100점" in r.text


def test_draft_endpoint_404_for_unknown_doc(client):
    assert client.post("/doc/999/draft").status_code == 404


def test_review_prompt_differs_by_doc_type():
    from zzaimy.app.pipeline import pick_review_prompt

    grant = pick_review_prompt("grant")
    recruit = pick_review_prompt("recruit")
    assert grant != recruit
    assert "지원자" in recruit
    assert "행정" in grant


def _uploaded(client):
    client.post("/upload", files={"file": ("a.pdf", b"%PDF fake", "application/pdf")})
    return client


def test_decision_approve(client):
    _uploaded(client)
    r = client.post("/doc/1/decision", data={"decision": "approved"}, follow_redirects=False)
    assert r.status_code == 303
    assert "승인" in client.get("/doc/1").text


def test_decision_reject(client):
    _uploaded(client)
    client.post("/doc/1/decision", data={"decision": "rejected"})
    assert "반려" in client.get("/doc/1").text


def test_decision_rework_triggers_re_review(client):
    _uploaded(client)
    client.post("/doc/1/decision", data={"decision": "rework"})
    page = client.get("/doc/1").text
    assert "재검토 의견: 담당자 요청 반영." in page  # FakeProcessor.reprocess 결과


def test_decision_rejects_unknown_value(client):
    _uploaded(client)
    r = client.post("/doc/1/decision", data={"decision": "??"})
    assert r.status_code == 400


def test_sector_tab_filters_documents(client):
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("공고문.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("이력서.pdf", b"%PDF", "application/pdf")})
    all_page = client.get("/").text
    assert "공고문.pdf" in all_page and "이력서.pdf" in all_page
    recruit_page = client.get("/?type=recruit").text
    # 표(문서함 목록) 기준으로 확인 — 사이드바 판정 대기 목록에는 둘 다 뜰 수 있다
    assert '<td><a href="/doc/2">이력서.pdf' in recruit_page
    assert '<td><a href="/doc/1">공고문.pdf' not in recruit_page


def test_password_protection_requires_auth(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), password="secret-1234",
    )
    c = TestClient(app)
    assert c.get("/").status_code == 401  # 인증 없이 거부
    assert c.get("/", auth=("zzaimy", "wrong")).status_code == 401
    assert c.get("/", auth=("zzaimy", "secret-1234")).status_code == 200


def test_no_password_means_open_localhost_mode(client):
    # password=None(기본)이면 로컬 전용 모드 — 인증 없이 동작 (기존 테스트와 동일)
    assert client.get("/").status_code == 200


def test_db_status_flow(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="x.pdf", stored_path="/tmp/x.pdf")
    doc = db.get_document(doc_id)
    assert doc["status"] == "received"
    db.update_document(doc_id, status="processing")
    assert db.get_document(doc_id)["status"] == "processing"
    db.add_review(doc_id, opinion="의견1")
    assert [r["opinion"] for r in db.get_reviews(doc_id)] == ["의견1"]


def test_hwpx_parsing_extracts_text(tmp_path):
    import zipfile

    from zzaimy.app.pipeline import DocumentProcessor

    hwpx = tmp_path / "합성.hwpx"
    with zipfile.ZipFile(hwpx, "w") as zf:
        zf.writestr(
            "Contents/section0.xml",
            "<hp:p><hp:t>합성 학칙 제1조 목적</hp:t></hp:p><hp:p><hp:t>본문이다</hp:t></hp:p>",
        )
    text = DocumentProcessor._parse_hwpx(hwpx)
    assert "합성 학칙 제1조 목적" in text
    assert "본문이다" in text
    assert "<hp:t>" not in text


def test_regulation_docs_are_separated_from_inbox(client):
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("계획서.pdf", b"%PDF", "application/pdf")})
    client.post("/criteria/upload",
                files={"file": ("학칙.pdf", b"%PDF", "application/pdf")})
    inbox = client.get("/").text
    assert "계획서.pdf" in inbox
    assert "학칙.pdf" not in inbox  # 기준 문서는 문서함에 안 섞인다
    criteria = client.get("/criteria").text
    assert "학칙.pdf" in criteria
    assert ">1건</span>" in criteria  # 등록 건수 1건 — 접수 문서는 기준 목록에 안 들어간다


class FakeResponder:
    def answer(self, db, question, attachment_text=None, criteria_ids=None, session_id=None):
        tail = f" / 첨부:{attachment_text}" if attachment_text else ""
        tail += f" / 기준:{sorted(criteria_ids)}" if criteria_ids else ""
        return f"합성 답변: {question[:20]}{tail}"


def test_chat_send_and_history(tmp_path):
    from zzaimy.app.main import create_app as _create

    app = _create(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    c = TestClient(app)
    r = c.get("/chat")
    assert r.status_code == 200
    r = c.post("/chat/send", data={"question": "휴학 처리 기준 알려줘"}, follow_redirects=False)
    assert r.status_code == 303
    session_url = r.headers["location"]
    page = c.get(session_url).text
    assert "휴학 처리 기준 알려줘" in page
    assert "합성 답변" in page
    # 사이드바 채팅 기록에 세션 제목 노출
    assert "휴학 처리 기준" in c.get("/").text


def test_chat_with_attachment_and_criteria(tmp_path):
    from zzaimy.app.main import create_app as _create

    app = _create(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    c = TestClient(app)
    # 기준 문서 하나 등록
    c.post("/criteria/upload", data={"sector": "recruit"},
           files={"file": ("채용공고.pdf", b"%PDF", "application/pdf")})
    r = c.post(
        "/chat/send",
        data={"question": "이 이력서 검토해줘", "criteria": "1"},
        files={"attachment": ("이력서.pdf", b"%PDF fake", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = c.get(r.headers["location"]).text
    assert "이력서.pdf" in page            # 첨부 표시
    assert "첨부:합성 첨부 본문" in page    # 첨부 텍스트가 응답기로 전달됨
    assert "기준:[1]" in page              # 선택한 기준이 전달됨


def test_sector_upload_binds_related_criteria(client):
    # 채용 공고를 기준으로 등록
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고A.pdf", b"%PDF", "application/pdf")})
    # 채용 섹터에서 해당 공고를 지정해 서류 접수
    client.post(
        "/upload",
        data={"doc_type": "recruit", "related_criteria_id": "1"},
        files={"file": ("지원서.pdf", b"%PDF", "application/pdf")},
    )
    page = client.get("/doc/2").text
    assert "채용공고A.pdf" in page  # 대상 공고가 문서 화면에 표시된다


def test_sector_page_offers_sector_criteria_options(client):
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고B.pdf", b"%PDF", "application/pdf")})
    client.post("/criteria/upload", data={"sector": "grant"},
                files={"file": ("국고공고.pdf", b"%PDF", "application/pdf")})
    page = client.get("/?type=recruit").text
    assert "채용공고B.pdf" in page       # 채용 섹터 접수 폼에 채용 공고 옵션
    assert "국고공고.pdf" not in page    # 다른 섹터 기준은 안 나온다


def test_receipt_number_scheme(client):
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("b.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("c.pdf", b"%PDF", "application/pdf")})
    page = client.get("/").text
    assert "2026-채용-0001" in page
    assert "2026-채용-0002" in page
    assert "2026-국고-0001" in page  # 섹터별 독립 일련번호
