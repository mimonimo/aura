"""문서 원본 열기 — 플랫폼 파일은 그대로, DGX 원본은 읽기 전용으로 받아 오고, 권한 없는 문서는 내주지 않는다."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import archive, archive_original
from zzaimy.app.main import create_app


def _app(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"})
    return app, c


def test_dgx_original_is_fetched_and_private_is_hidden(tmp_path, monkeypatch):
    app, c = _app(tmp_path)
    db = app.state.db
    did = db.add_document("보고서.pdf", "dgx://링크/2024/보고서.pdf", doc_type="grant", access_level="public")
    archive.ensure(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO archive_files (rel, size, mtime, removed_at) VALUES (?, ?, ?, '')", ("링크/2024/보고서.pdf", 4, 1))
    got = tmp_path / "fetched.bin"
    got.write_bytes(b"%PDF")
    monkeypatch.setattr(archive_original, "fetch", lambda row: got)
    r = c.get(f"/doc/{did}/original")                         # DGX 원본은 뒤에서 받아 오고 기다림 화면을 준다
    assert r.status_code == 200 and "원본 받기" in r.text and f"/doc/{did}/original/status" in r.text
    for _ in range(100):
        if c.get(f"/doc/{did}/original/status").json()["state"] != "running":
            break
        time.sleep(0.05)
    assert c.get(f"/doc/{did}/original/status").json()["state"] == "done"
    r = c.get(f"/doc/{did}/original")
    assert r.status_code == 200 and r.content == b"%PDF" and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert not got.exists()                                   # 받아 온 임시 파일은 캐시로 옮겨 남지 않는다
    r = c.get(f"/doc/{did}/original/download")               # 내려받기는 받아 둔 사본을 그대로
    assert r.status_code == 200 and r.content == b"%PDF" and r.headers["content-disposition"].startswith("attachment")
    secret = db.add_document("비밀.pdf", str(tmp_path / "x.pdf"), doc_type="grant", owner="other", access_level="owner")
    assert c.get(f"/doc/{secret}/original").status_code == 404
    lost = db.add_document("없는.pdf", "dgx://없는/파일.pdf", doc_type="grant", access_level="public")
    r = c.get(f"/doc/{lost}/original/download", follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]


def test_receipt_numbers_keep_counting_past_9999(tmp_path):
    from zzaimy.app.db import Database
    db = Database(tmp_path / "r.db")
    a = db.add_document("a.hwp", "x", doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET receipt_no = ? WHERE id = ?", (db.get_document(a)["receipt_no"].rsplit("-", 1)[0] + "-9999", a))
    b = db.add_document("b.hwp", "x", doc_type="grant")
    c = db.add_document("c.hwp", "x", doc_type="grant")
    nb, nc = db.get_document(b)["receipt_no"], db.get_document(c)["receipt_no"]
    assert nb.endswith("-10000") and nc.endswith("-10001")
