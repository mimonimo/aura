"""브라우저로 연 없는 주소·권한 밖 화면은 안내 화면으로, API 요청은 JSON 그대로."""

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
    assert r.status_code == 404 and "페이지를 찾을 수 없습니다" in r.text and "처음 화면으로" in r.text
    assert '{"detail"' not in r.text


def test_api_keeps_json_errors(tmp_path):
    client = make(tmp_path)
    r = client.get("/api/no-such", headers={"accept": "text/html"})
    assert r.status_code == 404 and r.json()["detail"]
    r = client.get("/no-such-page", headers={"accept": "application/json"})
    assert r.status_code == 404 and r.json()["detail"]
