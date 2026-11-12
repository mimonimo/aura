"""긴 대화(글 100개 넘음)에서도 「이번에 답을 남겼나」 를 바르게 본다 — 글 수로 보던 때는 list_chats 한도(100)에 걸려
답을 남기고도 「처리를 마쳤지만 남길 답을 만들지 못했습니다」 가 덧붙었다(11-12 특성화 재현, 대화 199)."""
from zzaimy.app.db import Database


def test_last_chat_id_grows_past_list_limit(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    sid = db.create_chat_session("긴 대화")
    assert sid
    assert db.last_chat_id(sid) == 0
    for i in range(105):
        db.add_chat(sid, "user" if i % 2 == 0 else "assistant", f"글 {i}")
    before = db.last_chat_id(sid)
    assert len(db.list_chats(sid)) == 100                # 한도에 걸려 수로는 늘지 않는다
    db.add_chat(sid, "assistant", "새 답")
    assert len(db.list_chats(sid)) == 100
    assert db.last_chat_id(sid) > before
