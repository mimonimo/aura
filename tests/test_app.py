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


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor()
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


def test_password_protection_requires_auth(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), password="secret-1234",
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
