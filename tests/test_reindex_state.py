"""규정 색인 맞춤 판정 — 조각이 어느 길로 바뀌었든 색인 메타의 조각 지문과 달라지면 다시 만들 일로 본다."""
import json

from zzaimy.app import paths, reindex_state
from zzaimy.app.db import Database


def test_needed_follows_chunk_changes(tmp_path):
    db = Database(tmp_path / "platform.db")
    did = db.add_document("학칙.pdf", "x", doc_type="regulation")
    with db._conn() as conn:
        conn.execute("INSERT INTO regulation_chunks (doc_id, reg_title, heading, content) VALUES (?, '학칙', '제1조', '목적')", (did,))
        sig = reindex_state.chunks_sig(conn)
    meta = paths.index_meta(tmp_path)
    meta.parent.mkdir(parents=True)
    assert reindex_state.needed(db, tmp_path)                      # 색인이 없다
    meta.write_text(json.dumps({"chunks_sig": sig}), encoding="utf-8")
    assert not reindex_state.needed(db, tmp_path)                  # 색인과 같다
    with db._conn() as conn:
        conn.execute("DELETE FROM regulation_chunks")
        conn.execute("INSERT INTO regulation_chunks (doc_id, reg_title, heading, content) VALUES (?, '학칙', '제1조', '목적 개정')", (did,))
    assert reindex_state.needed(db, tmp_path)                      # 지우고 다시 넣었다 — 표시 없이도 안다
