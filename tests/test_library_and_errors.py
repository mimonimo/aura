"""브라우저로 연 없는 주소는 안내 화면으로(API 는 JSON 그대로), 라이브러리 제목 찾기는 낱말마다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import main


def make(tmp_path):
    app = main.create_app(db_path=tmp_path / 'e.db', inbox_dir=tmp_path / 'inbox',
                          processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return TestClient(app)


def test_unknown_page_shows_error_screen_for_browser(tmp_path):
    client = make(tmp_path)
    r = client.get("/no-such-page", headers={"accept": "text/html"})
    assert r.status_code == 404 and "볼 수 없는 화면입니다" in r.text and "처음 화면으로" in r.text
    assert '{"detail"' not in r.text


def test_api_keeps_json_errors(tmp_path):
    client = make(tmp_path)
    r = client.get("/api/no-such", headers={"accept": "text/html"})
    assert r.status_code == 404 and r.json()["detail"]
    r = client.get("/no-such-page", headers={"accept": "application/json"})
    assert r.status_code == 404 and r.json()["detail"]


def test_library_title_search_matches_each_word(tmp_path):
    client = make(tmp_path)
    db = client.app.state.db
    db.add_document("가나 3.0 수정 사업계획서 핵심 성과지표 총괄", str(tmp_path / "a.pdf"), doc_type="grant")
    db.add_document("다른 문서", str(tmp_path / "b.pdf"), doc_type="grant")
    page = client.get("/?type=all&q=핵심성과지표 총괄").text
    assert "핵심 성과지표 총괄" in page and "다른 문서" not in page
    page = client.get("/?type=all&q=없는낱말").text
    assert "본문까지 찾기" in page and "body=1" in page                  # 라이브러리에서 본문까지(어휘 색인)
    page = client.get("/?type=all&q=없는낱말&body=1").text
    assert "대화·근거까지 찾기" in page and "/search?q=" in page
