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
    assert "이력서.pdf" in recruit_page
    assert "공고문.pdf" not in recruit_page


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
