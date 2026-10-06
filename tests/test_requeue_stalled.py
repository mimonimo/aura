"""재시작으로 끊긴 처리 — 받음으로 멈춘 플랫폼 문서는 다시 처리하고, DGX 원본·최근 문서는 건드리지 않는다."""
from __future__ import annotations

import time

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def test_requeue_stalled_docs(tmp_path):
    done = []

    class P(FakeProcessor):
        def process(self, db, doc_id, stored):
            done.append(doc_id)
            db.update_document(doc_id, status="reviewed")

    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=P(), drafter=FakeDrafter())
    db = app.state.db
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF")
    old = db.add_document("멈춘.pdf", str(f), doc_type="grant")
    dgx = db.add_document("원본.pdf", "dgx://링크/원본.pdf", doc_type="grant")
    new = db.add_document("방금.pdf", str(f), doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET created_at = '2026-10-02T14:00:00' WHERE id IN (?, ?)", (old, dgx))
    got = app.state.requeue_stalled_docs()
    assert got == [old]
    for _ in range(100):
        if db.get_document(old)["status"] == "reviewed":
            break
        time.sleep(0.05)
    assert done == [old] and db.get_document(old)["status"] == "reviewed"
    assert db.get_document(new)["status"] == "received" and db.get_document(dgx)["status"] == "received"
