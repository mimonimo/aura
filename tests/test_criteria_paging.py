"""문서함 접수 목록 — 100건씩 쪽으로, 찾기, 열람 권한은 SQL 에서(남의 담당자 한정 문서 이름이 목록에 나오지 않는다)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def test_intake_list_pages_searches_and_filters(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    db = app.state.db
    for i in range(105):
        db.add_document(f"공개문서{i:03d}.hwp", "x", doc_type="grant", access_level="public")
    db.add_document("남의비밀.hwp", "x", doc_type="grant", owner="other", access_level="owner")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"}, follow_redirects=False).status_code == 303
    r = c.get("/criteria")
    assert r.status_code == 200 and "105건" in r.text and "1 / 2쪽" in r.text
    assert "남의비밀" not in r.text and "공개문서104" in r.text and "공개문서000" not in r.text
    assert "공개문서000" in c.get("/criteria?ipage=1").text
    found = c.get("/criteria", params={"iq": "공개문서007"}).text
    assert "1건" in found and "공개문서007" in found
    d = TestClient(app)
    d.post("/login", data={"username": "zzdev", "pw": "devpass"})
    assert "106건" in d.get("/criteria").text          # 관리자는 전부
