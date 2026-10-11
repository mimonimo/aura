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


def test_search_body_hits_follow_rag_space(tmp_path, monkeypatch):
    """검색창의 본문 대목도 채팅 검색과 같은 공간 범위(ADR-0053)를 쓴다 — 사업 문서를 못 보는 공간이면 조회하지 않고,
    부서가 정해진 공간이면 그 부서만 넘긴다."""
    from zzaimy.app import access_guard, grant_lex

    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    calls = []
    monkeypatch.setattr(grant_lex, "rank", lambda *a, **k: calls.append(k.get("depts")) or [])
    c = TestClient(app)
    c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"})

    monkeypatch.setattr(access_guard, "search_scope", lambda *a, **k: {"grant": False, "grant_depts": [], "dept": "공통", "user": "zzaimy"})
    assert c.get("/search", params={"q": "감사보고"}).status_code == 200
    assert calls == []                                            # 사업 문서를 못 보는 공간 — 본문 색인을 묻지 않는다

    monkeypatch.setattr(access_guard, "search_scope", lambda *a, **k: {"grant": True, "grant_depts": ["가부서", "공통"], "dept": "가부서", "user": "zzaimy"})
    c.get("/search", params={"q": "감사보고"})
    assert calls == [["가부서", "공통"]]
