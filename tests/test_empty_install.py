"""새 설치(빈 문서함·빈 그래프·빈 규정 색인·기관 정보 없음)에서 주요 화면과 흐름이 오류 없이 열리는가 — 다른 대학에 깔아도(11-10)."""
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app.main import create_app


def test_main_screens_open_on_empty_install(tmp_path, monkeypatch):
    monkeypatch.delenv("ZZAIMY_INSTITUTION_NAME", raising=False)
    c = TestClient(create_app(db_path=tmp_path / "e.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                              drafter=FakeDrafter(), responder=FakeResponder()))
    bad = {}
    for path in ("/", "/chat", "/graph/explore", "/graph/program", "/library", "/team", "/programs"):
        r = c.get(path, follow_redirects=True)
        if r.status_code >= 500:
            bad[path] = r.status_code
    assert not bad, bad
    # 프로젝트 만들기 → 질문 — 문서가 하나도 없어도 끝까지
    r = c.post("/projects", data={"name": "빈 설치 시험", "sector": "grant"}, follow_redirects=True)
    assert r.status_code < 500
