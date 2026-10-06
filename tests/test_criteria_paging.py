"""라이브러리 문서 목록 — SQL 로 거르고 50건씩 쪽으로, 문서명·접수번호 찾기와 갈래 거르기,
열람 권한은 SQL 에서(남의 담당자 한정 문서 이름이 목록에 나오지 않는다). 문서함에 같던 목록은 2026-10-06 여기로 합쳤다."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def test_library_list_pages_searches_and_filters(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    db = app.state.db
    for i in range(105):
        d = db.add_document(f"공개문서{i:03d}.hwp", "x", doc_type="grant", access_level="public")
        if i < 3:
            db.set_document_kind(d, "plan")
    db.add_document("남의비밀.hwp", "x", doc_type="grant", owner="other", access_level="owner")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"}, follow_redirects=False).status_code == 303
    r = c.get("/?type=all")
    assert r.status_code == 200 and "등록 문서 105건" in r.text and "1–50 / 105건" in r.text
    assert "남의비밀" not in r.text and "공개문서104" in r.text and "공개문서000" not in r.text
    assert "공개문서000" in c.get("/?type=all&page=2").text
    found = c.get("/", params={"type": "all", "q": "공개문서007"}).text
    assert "검색 1건" in found and "공개문서007" in found
    plans = c.get("/", params={"type": "all", "kind": "plan"}).text
    assert "등록 문서 3건" in plans and "계획서 3" in plans
    d = TestClient(app)
    d.post("/login", data={"username": "zzdev", "pw": "devpass"})
    assert "등록 문서 106건" in d.get("/?type=all").text          # 관리자는 전부
