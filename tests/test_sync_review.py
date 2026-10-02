"""동기화 독립 검수. 알려진 결함은 strict xfail로 추적하며 수정 후 표시를 제거한다.

실제 DGX·VM·임베딩 서비스에 접속하지 않고 임시 DB와 잠금 파일만 사용한다.
재현: pytest tests/test_sync_review.py --runxfail
"""
import builtins
import fcntl
import importlib.util
from pathlib import Path

import pytest

from zzaimy.app import archive, grant_search
from zzaimy.app.db import Database


@pytest.fixture
def sync_job(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/170_vm_sync.py"
    spec = importlib.util.spec_from_file_location("sync_review", source)
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    monkeypatch.setattr(job, "ROOT", tmp_path)
    data = tmp_path / "data/platform"
    data.mkdir(parents=True)
    db = Database(data / "test.db")
    lock = tmp_path / "post.lock"

    def local_open(path, *args, **kwargs):
        return builtins.open(lock if str(path) == "/tmp/zz_post.lock" else path, *args, **kwargs)

    monkeypatch.setattr(job, "open", local_open, raising=False)
    monkeypatch.setattr(grant_search, "build_increment", lambda db: dict(added=0, removed=0, total=0, pending=0))
    monkeypatch.setattr(job.subprocess, "call", lambda *args, **kwargs: 0)
    return job, db, data, lock


@pytest.mark.xfail(strict=True, reason="C-181: 170 부모와 164 자식이 같은 후속 잠금을 획득")
def test_post_child_can_acquire_its_lock(sync_job, monkeypatch):
    job, db, data, lock = sync_job

    def child(*args, **kwargs):
        # 164의 별도 open + flock을 nonblocking으로 재현해 테스트가 멈추지 않게 한다.
        with lock.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return 0

    monkeypatch.setattr(job.subprocess, "call", child)
    assert job.post_if_changed(db) == 0


@pytest.mark.xfail(strict=True, reason="C-181: 색인 실패 후 dirty 재등록 누락")
def test_index_failure_remains_retryable(sync_job, monkeypatch):
    job, db, data, lock = sync_job

    def unavailable(db):
        raise RuntimeError("synthetic embedding outage")

    monkeypatch.setattr(grant_search, "build_increment", unavailable)
    job.post_if_changed(db)
    assert (data / ".kg-dirty").exists()


def test_pending_index_remains_retryable(sync_job, monkeypatch):
    job, db, data, lock = sync_job
    calls = []

    def pending(db):
        calls.append(True)
        return dict(added=1, removed=0, total=1, pending=1)

    monkeypatch.setattr(grant_search, "build_increment", pending)
    job.post_if_changed(db)
    assert (data / ".kg-dirty").exists()
    job.post_if_changed(db)
    assert len(calls) == 2


@pytest.mark.xfail(strict=True, reason="C-181: 후속 그래프 명령 실패 후 dirty 재등록 누락")
def test_graph_failure_remains_retryable(sync_job, monkeypatch):
    job, db, data, lock = sync_job
    monkeypatch.setattr(job.subprocess, "call", lambda *args, **kwargs: 1)
    assert job.post_if_changed(db) != 0
    assert not (data / "kg_marker.json").exists()
    assert (data / ".kg-dirty").exists()


@pytest.mark.xfail(strict=True, reason="C-181: dry에서도 후속 갱신 호출")
def test_dry_does_not_run_post_processing(sync_job, monkeypatch):
    job, db, data, lock = sync_job
    monkeypatch.setattr(job.sys, "argv", ["170_vm_sync.py", "--dry"])
    monkeypatch.setattr(job, "Database", lambda path: db)
    monkeypatch.setattr(job, "listing", lambda: [])
    calls = []
    monkeypatch.setattr(job, "post_if_changed", lambda db: calls.append("post") or 0)
    # 목록이 비어 있으므로 실제 파일 선택기를 불러올 필요가 없다.
    monkeypatch.setattr(job, "_sel", lambda: None)
    assert job.main() == 0
    assert calls == []


def test_ambiguous_move_does_not_assign_document_to_first_candidate(tmp_path):
    db = Database(tmp_path / "test.db")
    original = dict(rel="old/report.pdf", size=10, mtime=1)
    archive.sync(db, [original], {original["rel"]: 42})
    got = archive.sync(db, [{**original, "rel": "new-a/report.pdf"},
                            {**original, "rel": "new-b/report.pdf"}])
    assert got["moved"] == []
    assert all(r["doc_id"] is None for r in archive.find(db))


def test_sync_attaches_newly_ingested_document(tmp_path):
    db = Database(tmp_path / "test.db")
    rows = [dict(rel="program/report.pdf", size=10, mtime=1)]
    archive.sync(db, rows)
    archive.sync(db, rows, {rows[0]["rel"]: 42})
    assert archive.find(db)[0]["doc_id"] == 42


@pytest.mark.xfail(strict=True, reason="C-181: 원본 목록 화면에 개인 첨부 가시성 필터 없음")
def test_private_upload_path_is_not_visible_to_other_user(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login

    app = _app(tmp_path)
    db = app.state.db
    did = db.add_document("private-test.pdf", "unused", doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET owner='other', access_level='owner' WHERE id=?", (did,))
    rel = archive.UPLOAD_PREFIX + "private-test.pdf"
    archive.load(db, [dict(rel=rel, size=1, mtime=1)], {rel: did})
    client = TestClient(app)
    assert _login(client, "zzaimy", "boot-pass-1")
    response = client.get("/archive", params={"q": "private-test"})
    assert response.status_code in (200, 403, 404)
    assert "private-test.pdf" not in response.text
