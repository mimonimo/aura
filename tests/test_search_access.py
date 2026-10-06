"""통합 검색(/search)은 열람 권한을 지킨다 — 남의 담당자 한정 문서는 이름도 본문 조각도 나오지 않는다."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def test_search_hides_others_private_documents(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    db = app.state.db
    db.add_document("감사보고 공개본.hwp", "x", doc_type="grant", access_level="public")
    db.add_document("감사보고 비밀본.hwp", "x", doc_type="grant", owner="other", access_level="owner")
    c = TestClient(app)
    c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"})
    r = c.get("/search", params={"q": "감사보고"})
    assert r.status_code == 200 and "공개본" in r.text and "비밀본" not in r.text
    d = TestClient(app)
    d.post("/login", data={"username": "zzdev", "pw": "devpass"})
    assert "비밀본" in d.get("/search", params={"q": "감사보고"}).text
