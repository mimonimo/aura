from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def test_progress_is_live_scoped_and_cleared(tmp_path):
    observed = []

    class Responder:
        def answer(self, db, question, on_progress=None, **kwargs):
            on_progress("관련 근거 검색 중")
            on_progress("관련 근거 검색 중")
            observed.append(client.get("/chat/1/status").json())
            assert 'id="chatProgress"' in client.get("/chat/1").text
            assert client.get("/chat/999/status").status_code == 404
            on_progress("답변 작성 중")
            return "완료"

    app = create_app(db_path=tmp_path / "chat.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=Responder())
    client = TestClient(app)
    assert client.post("/chat/send", data={"question": "참여 목표는?"}).status_code == 200
    state = observed[0]
    assert state["waiting"] is True
    assert state["progress"]["started_at"] > 0
    assert state["progress"]["steps"][-1] == "관련 근거 검색 중"
    assert state["progress"]["steps"].count("관련 근거 검색 중") == 1
    assert client.get("/chat/1/status").json() == {"waiting": False, "progress": None}


def test_progress_cleared_on_responder_failure(tmp_path):
    class Responder:
        def answer(self, db, question, on_progress=None, **kwargs):
            on_progress("관련 근거 검색 중")
            raise RuntimeError("test failure")

    app = create_app(db_path=tmp_path / "chat.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=Responder())
    client = TestClient(app)
    client.post("/chat/send", data={"question": "참여 목표는?"})
    assert client.get("/chat/1/status").json() == {"waiting": False, "progress": None}
