"""프로젝트 작업 폴더의 부서 보기 공유 — 동료 메일에 보기 권한을 주고, 끄거나 동료가 아니게 되면 이 기능이 준 것만 거둔다."""
from __future__ import annotations

import json
from types import SimpleNamespace

from zzaimy.app.db import Database
from zzaimy.ingest import drive_share, gdrive_files


class FakeHttp:
    def __init__(self):
        self.posts, self.deletes, self.n = [], [], 0

    def post(self, url, headers=None, params=None, json=None):
        self.n += 1
        self.posts.append((url, json, dict(params or {})))
        return SimpleNamespace(status_code=200, json=lambda: {"id": f"p{self.n}"})

    def delete(self, url, headers=None, params=None):
        self.deletes.append(url)
        return SimpleNamespace(status_code=204)


def _setup(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda e: True)
    monkeypatch.setattr(gdrive_files, "project_folder_for", lambda db, e, p, d, sub="", http=None: "FOLDER")
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: {})
    pid = db.create_project("grant", "공유 시험", owner="kim")
    return db, db.get_project(pid)


def test_share_grants_reader_to_colleagues_and_revokes_only_its_own(tmp_path, monkeypatch):
    db, proj = _setup(tmp_path, monkeypatch)
    http = FakeHttp()
    r = drive_share.sync(db, proj, "kim@ync.ac.kr", ["lee@ync.ac.kr", "park@ync.ac.kr", "kim@ync.ac.kr"], http=http)
    assert sorted(r["added"]) == ["lee@ync.ac.kr", "park@ync.ac.kr"] and r["shared"] == 2       # 담당자 본인은 빼고
    url, body, params = http.posts[0]
    assert url.endswith("/files/FOLDER/permissions") and body["role"] == "reader" and body["type"] == "user"
    assert params["sendNotificationEmail"] == "false"
    # 다시 맞춰도 이미 준 것은 또 주지 않는다
    assert drive_share.sync(db, proj, "kim@ync.ac.kr", ["lee@ync.ac.kr", "park@ync.ac.kr"], http=http)["added"] == []
    # 동료가 아니게 된 메일만 거둔다 — 이 기능이 준 권한 id 로
    r = drive_share.sync(db, proj, "kim@ync.ac.kr", ["lee@ync.ac.kr"], http=http)
    assert r["removed"] == ["park@ync.ac.kr"] and len(http.deletes) == 1 and http.deletes[0].endswith("/permissions/p2")
    # 끄면 준 것을 모두 거둔다
    drive_share.set_enabled(db, proj["id"], False)
    r = drive_share.sync(db, proj, "kim@ync.ac.kr", ["lee@ync.ac.kr"], http=http)
    assert r["removed"] == ["lee@ync.ac.kr"] and r["shared"] == 0 and drive_share.state(db, proj["id"])["on"] is False


def test_share_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    db, proj = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda e: False)
    r = drive_share.sync(db, proj, "kim@ync.ac.kr", ["lee@ync.ac.kr"], http=FakeHttp())
    assert r["added"] == [] and "연결되지 않아" in r["error"]
    assert "연결되지 않아" in drive_share.state(db, proj["id"])["error"]
    assert drive_share.enabled(db, proj["id"]) is True                     # 기본은 켬


def test_nothing_to_share_never_touches_drive(tmp_path, monkeypatch):
    db, proj = _setup(tmp_path, monkeypatch)
    touched = []
    monkeypatch.setattr(gdrive_files, "project_folder_for", lambda *a, **k: touched.append(1) or "F")
    r = drive_share.sync(db, proj, "kim@ync.ac.kr", [], http=FakeHttp())
    assert touched == [] and r["shared"] == 0 and r["error"] == ""
