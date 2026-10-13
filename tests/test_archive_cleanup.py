"""원본 보관소 정리 — 처리하지 않는 원본을 갈래별로, 정리함으로 옮기면 보관소 건수에서 빠지고 되돌리면 돌아온다."""
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import archive, archive_cleanup, metrics


def _put(db, rows):
    archive.ensure(db)
    with db._conn() as conn:
        for rel, ext, size, dup in rows:
            conn.execute("INSERT INTO archive_files (rel, size, mtime, ext, dup_of) VALUES (?, ?, 0, ?, ?)", (rel, size, ext, dup))


def test_cleanup_categories_move_and_restore(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    _put(db, [("링크/사진/a.jpg", "jpg", 100, ""), ("링크/사진/b.png", "png", 50, ""), ("링크/묶음.zip", "zip", 10, ""),
              ("링크/지출/영수증.pdf", "pdf", 5, ""), ("링크/계획서.hwp", "hwp", 7, ""), ("링크/계획서 사본.hwp", "hwp", 7, "링크/계획서.hwp")])
    s = {x["key"]: x for x in archive_cleanup.summary(db, force=True)["items"]}
    assert (s["photo"]["n"], s["photo"]["bytes"], s["archive"]["n"], s["scope"]["n"], s["dup"]["n"]) == (2, 150, 1, 1, 1)
    before = metrics.live(db, force=True)["values"]["archive_originals"]
    assert archive_cleanup.move(db, "photo", by="zzdev") == 2
    assert metrics.live(db, force=True)["values"]["archive_originals"] == before - 2      # 보관소 건수에서 빠진다
    s = {x["key"]: x for x in archive_cleanup.summary(db, force=True)["items"]}
    assert s["photo"]["n"] == 0 and s["photo"]["cleaned"] == 2
    assert archive_cleanup.restore(db, "photo") == 2
    assert metrics.live(db, force=True)["values"]["archive_originals"] == before


def test_cleanup_routes_are_admin_only(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    assert c.get("/archive/cleanup").status_code == 403
    assert c.post("/archive/cleanup/move", json={"key": "photo"}).status_code == 403
    assert 'id="archive-cleanup"' not in c.get("/archive").text
