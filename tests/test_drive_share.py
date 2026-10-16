"""프로젝트 작업 문서의 부서 공유 — 문서마다 동료 메일에 보기 권한, 목표에서 빠지면 이 기능이 준 것만 거둔다."""
from __future__ import annotations

from types import SimpleNamespace

from zzaimy.app.db import Database
from zzaimy.ingest import drive_share, gdrive_files


class FakeHttp:
    def __init__(self, fail_delete=False):
        self.posts, self.deletes, self.n, self.fail_delete = [], [], 0, fail_delete

    def post(self, url, headers=None, params=None, json=None):
        self.n += 1
        self.posts.append((url, json, dict(params or {})))
        return SimpleNamespace(status_code=200, json=lambda: {"id": f"p{self.n}"})

    def delete(self, url, headers=None, params=None):
        self.deletes.append(url)
        return SimpleNamespace(status_code=500 if self.fail_delete else 204)


def _setup(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda e: True)
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: {})
    return db, db.create_project("grant", "공유 시험", owner="kim")


def test_share_per_document_and_revoke_only_own_grants(tmp_path, monkeypatch):
    db, pid = _setup(tmp_path, monkeypatch)
    http = FakeHttp()
    r = drive_share.sync(db, pid, "kim@ync.ac.kr", {"D1": {"lee@ync.ac.kr", "kim@ync.ac.kr"}, "D2": {"lee@ync.ac.kr", "park@ync.ac.kr"}}, http=http)
    assert sorted(r["added"]) == ["D1|lee@ync.ac.kr", "D2|lee@ync.ac.kr", "D2|park@ync.ac.kr"]      # 담당자 본인은 빼고, 문서마다
    url, body, params = http.posts[0]
    assert "/files/D1/permissions" in url and body["role"] == "reader" and params["sendNotificationEmail"] == "false"
    st = drive_share.state(db, pid)
    assert st["shared"] == 2 and st["files"] == 2
    # D2 가 목표에서 빠지면(비공개로 돌린 대화 등) D2 의 권한만 거둔다
    r = drive_share.sync(db, pid, "kim@ync.ac.kr", {"D1": {"lee@ync.ac.kr"}}, http=http)
    assert sorted(r["removed"]) == ["D2|lee@ync.ac.kr", "D2|park@ync.ac.kr"] and r["added"] == []
    # 끄면 남은 것도 모두 거둔다
    drive_share.set_enabled(db, pid, False)
    r = drive_share.sync(db, pid, "kim@ync.ac.kr", {"D1": {"lee@ync.ac.kr"}}, http=http)
    assert r["removed"] == ["D1|lee@ync.ac.kr"] and r["shared"] == 0


def test_failed_revocation_stays_recorded_for_retry(tmp_path, monkeypatch):
    db, pid = _setup(tmp_path, monkeypatch)
    drive_share.sync(db, pid, "kim@ync.ac.kr", {"D1": {"lee@ync.ac.kr"}}, http=FakeHttp())
    r = drive_share.sync(db, pid, "kim@ync.ac.kr", {}, http=FakeHttp(fail_delete=True))
    assert r["error"] and drive_share.has_grants(db, pid)                  # 거두지 못한 권한은 기록에 남아 다음에 다시
    assert drive_share.sync(db, pid, "kim@ync.ac.kr", {}, http=FakeHttp())["removed"] == ["D1|lee@ync.ac.kr"]


def test_nothing_to_share_never_touches_drive(tmp_path, monkeypatch):
    db, pid = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda e: (_ for _ in ()).throw(AssertionError("드라이브를 건드림")))
    r = drive_share.sync(db, pid, "kim@ync.ac.kr", {}, http=FakeHttp())
    assert r["shared"] == 0 and r["error"] == "" and drive_share.enabled(db, pid) is True       # 기본은 켬


def test_revocation_uses_the_granting_account_and_disconnect_revokes(tmp_path, monkeypatch):
    """거둘 때는 권한을 준 구글 계정으로 — 담당자 구글이 바뀌어도 옛 계정이 준 권한을 거둔다. 연결을 끊기 전 revoke_by 로 모두 거둔다."""
    db, pid = _setup(tmp_path, monkeypatch)
    used = []
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: used.append(e) or {})
    drive_share.sync(db, pid, "old@ync.ac.kr", {"D1": {"lee@ync.ac.kr"}}, http=FakeHttp())
    used.clear()
    r = drive_share.sync(db, pid, "new@ync.ac.kr", {}, http=FakeHttp())
    assert r["removed"] == ["D1|lee@ync.ac.kr"] and used == ["old@ync.ac.kr"]
    drive_share.sync(db, pid, "kim@ync.ac.kr", {"D2": {"lee@ync.ac.kr"}}, http=FakeHttp())
    assert drive_share.revoke_by(db, pid, "kim@ync.ac.kr", http=FakeHttp()) is True and not drive_share.has_grants(db, pid)


def test_plan_function_is_evaluated_inside_the_lock(tmp_path, monkeypatch):
    db, pid = _setup(tmp_path, monkeypatch)
    seen = []
    def plan():
        seen.append(drive_share._lock(pid).locked())
        return {"D1": {"lee@ync.ac.kr"}}, 1
    r = drive_share.sync(db, pid, "kim@ync.ac.kr", plan, http=FakeHttp())
    assert seen == [True] and r["added"] == ["D1|lee@ync.ac.kr"] and drive_share.state(db, pid)["missing"] == 1
