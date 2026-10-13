"""관리자 실험 — 대화 질문 하나의 사업 문서 검색 방식을 고른다(GraphRAG 비교, scripts/188). 기본은 운영 그대로, 관리자만."""
import time

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import grant_search
from zzaimy.app.main import create_app


class ModeResponder:
    def __init__(self):
        self.seen = []

    def answer(self, db, question, **kw):
        self.seen.append((question, grant_search.GRAPH_MODE.get()))
        return "합성 답변"


def _wait(c, url, r):
    for _ in range(50):
        if r.seen:
            return
        time.sleep(0.1)


def _app(tmp_path, password=None):
    r = ModeResponder()
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=r, password=password)
    return app, r


def test_dev_can_pick_fulltext_for_one_question(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "0")
    app, r = _app(tmp_path)                                  # 비밀번호 없음 = 로컬 관리자
    c = TestClient(app)
    assert 'name="retrieval"' in c.get("/chat").text
    res = c.post("/chat/send", data={"question": "취업률 목표 알려줘", "retrieval": "fulltext"}, follow_redirects=False)
    _wait(c, res.headers["location"], r)
    assert r.seen == [("취업률 목표 알려줘", "fulltext")]      # 질문은 그대로, 검색만 실험 모드
    assert "풀텍스트 RAG" in c.get(res.headers["location"]).text
    assert grant_search.GRAPH_MODE.get() == ""


def test_default_and_non_dev_stay_on_production_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "0")
    app, r = _app(tmp_path, password="pw-1234")
    c = TestClient(app)
    auth = ("zzaimy", "pw-1234")                              # 담당자(staff) 계정
    assert 'name="retrieval"' not in c.get("/chat", auth=auth).text
    res = c.post("/chat/send", data={"question": "취업률 목표 알려줘", "retrieval": "fulltext"}, auth=auth,
                 follow_redirects=False)
    _wait(c, res.headers["location"], r)
    assert r.seen == [("취업률 목표 알려줘", "")]
