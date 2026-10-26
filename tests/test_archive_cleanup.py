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


def test_keep_drops_from_cleanup_list_but_stays_in_archive(tmp_path):
    """남기기 — 정리 목록에서만 빠지고 보관소 건수는 그대로. 요약은 남은 수·크기와 지난 정리 시각을 준다."""
    app = _app(tmp_path)
    db = app.state.db
    _put(db, [("링크/사진/a.jpg", "jpg", 100, ""), ("링크/묶음.zip", "zip", 10, "")])
    s = archive_cleanup.summary(db, force=True)
    assert (s["left"], s["left_bytes"], s["last_at"], s["kept_total"]) == (2, 110, "", 0)
    before = metrics.live(db, force=True)["values"]["archive_originals"]
    assert archive_cleanup.keep(db, "archive", by="zzdev") == 1
    s = archive_cleanup.summary(db, force=True)
    assert s["left"] == 1 and s["kept_total"] == 1 and {x["key"]: x["n"] for x in s["items"]}["archive"] == 0
    assert metrics.live(db, force=True)["values"]["archive_originals"] == before          # 보관소에는 그대로
    archive_cleanup.move(db, "photo")
    s = archive_cleanup.summary(db, force=True)
    assert s["left"] == 0 and s["last_at"] and s["restorable"] == 1


def test_cleanup_routes_take_several_keys(tmp_path):
    app = _app(tmp_path)
    _put(app.state.db, [("링크/사진/a.jpg", "jpg", 100, ""), ("링크/묶음.zip", "zip", 10, "")])
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    d = c.post("/archive/cleanup/move", json={"keys": ["photo", "archive"]}).json()
    assert d["moved"] == 2 and d["left"] == 0
    assert c.post("/archive/cleanup/restore", json={"key": ""}).json()["restored"] == 2
    assert c.post("/archive/cleanup/keep", json={"keys": ["photo"]}).json()["kept"] == 1
    page = c.get("/archive").text
    assert 'id="acActs" hidden' in page and 'data-do="keep"' in page            # 줄 선택 → 작업 줄(정리함으로·남기기·선택 해제)


def test_staff_do_not_see_processing_state(tmp_path):
    app = _app(tmp_path)
    _put(app.state.db, [("링크/사진/a.jpg", "jpg", 100, "")])
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    page = c.get("/archive").text
    assert 'aria-label="처리 상태"' not in page and "처리 제외" not in page
    assert _login(c, "zzdev", "devpass")
    assert 'aria-label="처리 상태"' in c.get("/archive").text
