"""대화 요청 대기열(11-12) — 답을 쓰는 동안 보낸 요청은 버리지 않고 차례로 처리한다."""
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import chat_queue
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


def test_queue_add_pop_drop_clear_and_limit(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "q.db"))
    assert chat_queue.pop(db, 7) is None
    for i in range(3):
        chat_queue.add(db, 7, {"q": f"요청 {i}\n둘째 줄", "user": "a"})
    p = chat_queue.preview(db, 7)
    assert p["queued"] == 3 and p["next"][0] == {"i": 0, "text": "요청 0"}
    assert chat_queue.drop(db, 7, 1) and not chat_queue.drop(db, 7, 5)
    assert [x["q"].split("\n")[0] for x in chat_queue.items(db, 7)] == ["요청 0", "요청 2"]
    assert chat_queue.pop(db, 7)["q"].startswith("요청 0")
    assert chat_queue.waiting_sessions(db) == [7]
    assert chat_queue.clear(db, 7) == 1 and chat_queue.items(db, 7) == [] and chat_queue.waiting_sessions(db) == []
    monkeypatch.setattr(chat_queue, "LIMIT", 2)
    chat_queue.add(db, 8, {"q": "a", "user": "a"})
    chat_queue.add(db, 8, {"q": "b", "user": "a"})
    with pytest.raises(ValueError):
        chat_queue.add(db, 8, {"q": "c", "user": "a"})


def _wait(cond, limit=10.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_request_sent_while_answering_is_queued_then_runs(tmp_path):
    seen = []

    class Responder:
        def answer(self, db, question, on_progress=None, **kwargs):
            seen.append(question)
            if question == "첫째":
                # 답을 쓰는 중에 다음 요청 둘 — 버리지 않고 대기열로
                r = client.post("/chat/send", data={"question": "둘째", "session_id": 1}, headers={"accept": "application/json"})
                assert r.json() == {"ok": True, "queued": 1}
                client.post("/chat/send", data={"question": "셋째", "session_id": 1})
                st = client.get("/chat/1/status").json()
                assert st["waiting"] is True and st["queued"] == 2 and [x["text"] for x in st["next"]] == ["둘째", "셋째"]
            return f"{question} 답"

    app = create_app(db_path=tmp_path / "chat.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=Responder())
    client = TestClient(app)
    client.post("/chat/send", data={"question": "첫째"})
    db = app.state.chat.db
    assert _wait(lambda: len(db.list_chats(1)) == 6 and not client.get("/chat/1/status").json()["waiting"])
    assert seen == ["첫째", "둘째", "셋째"]
    assert [m["content"] for m in db.list_chats(1)] == ["첫째", "첫째 답", "둘째", "둘째 답", "셋째", "셋째 답"]
    assert client.get("/chat/1/status").json()["queued"] == 0


def test_queue_clear_and_drop_routes(tmp_path):
    app = create_app(db_path=tmp_path / "chat.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    client = TestClient(app)
    db = app.state.chat.db
    sid = db.create_chat_session("대화")
    for q in ("가", "나", "다"):
        chat_queue.add(db, sid, {"q": q, "user": "zzaimy"})
    assert client.post(f"/chat/{sid}/queue/1/drop").json()["queued"] == 2
    assert client.post(f"/chat/{sid}/queue/clear").json() == {"ok": True, "dropped": 2, "queued": 0, "next": []}
    assert client.post("/chat/999/queue/clear").status_code == 404


def test_queued_request_of_someone_without_access_is_dropped(tmp_path):
    app = create_app(db_path=tmp_path / "chat.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    from zzaimy.app import chat_routes
    db = app.state.chat.db
    sid = db.create_chat_session("대화", owner="a")
    chat_queue.add(db, sid, {"q": "남의 요청", "user": "b"})
    assert chat_routes.start_queued(app.state.chat, sid) is False
    assert chat_queue.items(db, sid) == [] and db.list_chats(sid) == []
