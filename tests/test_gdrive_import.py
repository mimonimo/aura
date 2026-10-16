"""구글 드라이브 자동 가져오기 — 「ZZAIMY 가져오기」 폴더의 새·바뀐 파일만, 구글 문서는 docx 로, 못 읽는 형식은 건너뛴다."""
from __future__ import annotations

from types import SimpleNamespace

from zzaimy.app import gdrive_import
from zzaimy.app.db import Database
from zzaimy.ingest import gdrive_files


class FakeHttp:
    def __init__(self, files):
        self.files = files
        self.downloads = []

    def get(self, url, headers=None, params=None):
        if url.endswith("/files"):
            return SimpleNamespace(status_code=200, json=lambda: {"files": self.files})
        self.downloads.append((url, dict(params or {})))
        return SimpleNamespace(status_code=200, content=b"%PDF-1.4 data")


class FakeProcessor:
    def __init__(self):
        self.done = []

    def process(self, db, doc_id, stored):
        self.done.append(doc_id)
        db.update_document(doc_id, status="reviewed")


def test_imports_new_and_changed_files_only(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    monkeypatch.setattr(gdrive_files, "ensure_folder", lambda email, parts, http=None, root="root": "F1")
    monkeypatch.setattr(gdrive_files, "_headers", lambda email, http: {})
    monkeypatch.setattr("zzaimy.app.storage.adopt_original", lambda db, did, p: p)
    files = [{"id": "a", "name": "계획서.pdf", "mimeType": "application/pdf", "modifiedTime": "t1", "size": "10"},
             {"id": "b", "name": "회의록", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "t1"},
             {"id": "c", "name": "사진.heic", "mimeType": "image/heic", "modifiedTime": "t1", "size": "10"},
             {"id": "d", "name": "하위폴더", "mimeType": "application/vnd.google-apps.folder", "modifiedTime": "t1"}]
    http, proc = FakeHttp(files), FakeProcessor()
    got = gdrive_import.run_for_user(db, proc, tmp_path / "inbox", "kim", "kim@example.ac.kr", http=http)
    names = sorted(x["name"] for x in got["imported"])
    assert names == ["계획서.pdf", "회의록.docx"] and len(proc.done) == 2
    assert any("export" in u for u, _ in http.downloads)                 # 구글 문서는 내보내기로
    assert got["errors"] == ["사진.heic: 가져올 수 없는 형식"]
    doc = db.get_document(got["imported"][0]["doc_id"])
    assert doc["owner"] == "kim"
    # 다시 돌리면 그대로인 것은 건너뛰고, 바뀐 것만 다시
    files[0]["modifiedTime"] = "t2"
    again = gdrive_import.run_for_user(db, proc, tmp_path / "inbox", "kim", "kim@example.ac.kr", http=http)
    assert [x["name"] for x in again["imported"]] == ["계획서.pdf"] and again["skipped"] == 2
    st = gdrive_import.status(db, "kim")
    assert st["imported_total"] == 3 and st["folder_id"] == "F1"


def test_bound_users_lists_personal_connections_only(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_setting("google_account:kim", "kim@example.ac.kr")
    db.set_setting("google_account_dept:산학협력단", "shared@example.ac.kr")
    assert gdrive_import.bound_users(db) == [("kim", "kim@example.ac.kr")]


def test_same_google_account_imports_once(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    db.set_setting("google_account:zzdev", "s@example.ac.kr")
    db.set_setting("google_account:kim", "s@example.ac.kr")
    monkeypatch.setattr(gdrive_files, "ensure_folder", lambda email, parts, http=None, root="root": "F1")
    monkeypatch.setattr(gdrive_files, "_headers", lambda email, http: {})
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda email: True)
    monkeypatch.setattr("zzaimy.app.storage.adopt_original", lambda db, did, p: p)
    http = FakeHttp([{"id": "a", "name": "계획서.pdf", "mimeType": "application/pdf", "modifiedTime": "t1", "size": "10"}])
    monkeypatch.setattr("zzaimy.ingest.gdrive._http", lambda: http)
    out = gdrive_import.run_all(db, FakeProcessor(), tmp_path / "inbox", rank=lambda u: 1 if u == "zzdev" else 0)
    assert out == {"kim": 1}                                     # 담당자 계정이 주인, 한 번만
    assert gdrive_import.run_all(db, FakeProcessor(), tmp_path / "inbox") == {"kim": 0}


def test_check_now_runs_once_in_background_and_throttles(tmp_path, monkeypatch):
    """화면을 열 때 곧바로 한 번(뒤에서) — 20초 안에 다시 열면 건너뛰고, 연결이 없으면 하지 않는다. 주기는 기본 1분."""
    import time as _t
    db = Database(tmp_path / "t.db")
    monkeypatch.setattr(gdrive_files, "ensure_folder", lambda email, parts, http=None, root="root": "F1")
    monkeypatch.setattr(gdrive_files, "_headers", lambda email, http: {})
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda email: True)
    monkeypatch.setattr("zzaimy.app.storage.adopt_original", lambda db, did, p: p)
    http = FakeHttp([{"id": "a", "name": "새공고.pdf", "mimeType": "application/pdf", "modifiedTime": "t1", "size": "10"}])
    monkeypatch.setattr("zzaimy.ingest.gdrive._http", lambda: http)
    assert gdrive_import.INTERVAL == 60 or "ZZAIMY_GIMPORT_INTERVAL" in __import__("os").environ
    assert gdrive_import.check_now(db, FakeProcessor(), tmp_path / "inbox", "nobody") == "none"
    db.set_setting("google_account:kim", "kim@example.ac.kr")
    assert gdrive_import.check_now(db, FakeProcessor(), tmp_path / "inbox", "kim") == "started"
    for _ in range(50):
        if gdrive_import.status(db, "kim").get("recent"):
            break
        _t.sleep(0.05)
    recent = gdrive_import.status(db, "kim")["recent"]
    assert [r["name"] for r in recent] == ["새공고.pdf"]
    assert gdrive_import.check_now(db, FakeProcessor(), tmp_path / "inbox", "kim") == "recent"     # 20초 안에는 다시 안 함
