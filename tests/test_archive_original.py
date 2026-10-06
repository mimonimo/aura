import subprocess

import pytest
from fastapi.testclient import TestClient
from tests.test_accounts import _app, _login
from zzaimy.app import archive, archive_original


@pytest.fixture
def setup(tmp_path):
    app = _app(tmp_path)
    archive.load(app.state.db, [
        {"rel": "사업/계획서.pdf", "size": 4, "mtime": 1, "program": "program:test", "program_name": "시험 사업"},
        {"rel": "사업/서식.hwp", "size": 4, "mtime": 1},
    ])
    client = TestClient(app)
    assert _login(client, "zzdev", "devpass")
    return app, client


def test_archive_layout_and_zero_denominator(setup):
    app, client = setup
    page = client.get("/archive?q=계획서").text
    assert "문서함 연결률" in page and "0.0%" in page
    assert "OCR·분석 완료율" in page
    assert page.index('id="archive-search"') < page.index("사업별 보관 현황")
    assert "원본 열기" in page and "계획서.pdf" in page
    with app.state.db._conn() as conn:
        conn.execute("DELETE FROM archive_files")
    assert client.get("/archive").status_code == 200


@pytest.mark.parametrize("rel,mime,disposition", [
    ("사업/계획서.pdf", "application/pdf", "inline"),
    ("사업/서식.hwp", "application/octet-stream", "attachment"),
])
def test_original_response_and_cleanup(setup, tmp_path, monkeypatch, rel, mime, disposition):
    _, client = setup
    file = tmp_path / "temporary"
    file.write_bytes(b"test")
    monkeypatch.setattr(archive_original, "fetch", lambda row: file)
    response = client.get("/archive/original", params={"rel": rel})
    assert response.status_code == 200 and response.content == b"test"
    assert response.headers["content-type"] == mime
    assert response.headers["content-disposition"].startswith(disposition)
    assert not file.exists()


def test_missing_and_removed_never_fetch(setup, monkeypatch):
    app, client = setup
    monkeypatch.setattr(archive_original, "fetch", lambda row: pytest.fail("must not fetch"))
    assert client.get("/archive/original?rel=../../etc/passwd").status_code == 404
    with app.state.db._conn() as conn:
        conn.execute("UPDATE archive_files SET removed_at = 'removed'")
    assert client.get("/archive/original", params={"rel": "사업/계획서.pdf"}).status_code == 404


def test_private_document_and_unlinked_denied(setup, monkeypatch):
    app, client = setup
    assert _login(client, "zzaimy", "boot-pass-1")
    monkeypatch.setattr(archive_original, "fetch", lambda row: pytest.fail("must not fetch"))
    assert client.get("/archive/original", params={"rel": "사업/서식.hwp"}).status_code == 403
    from zzaimy.app import access_policy
    monkeypatch.setattr(access_policy, "visible", lambda *a, **kw: False)
    with app.state.db._conn() as conn:
        conn.execute("UPDATE archive_files SET doc_id = 999 WHERE rel = '사업/계획서.pdf'")
    assert client.get("/archive/original", params={"rel": "사업/계획서.pdf"}).status_code == 403


@pytest.mark.parametrize("rel,size", [("../escape", 1), ("/etc/passwd", 1), ("a\\b", 1), ("a", 100_000_001)])
def test_fetch_rejects_path_and_size(rel, size):
    with pytest.raises(ValueError):
        archive_original.fetch({"rel": rel, "size": size, "mtime": 1})


def test_fetch_timeout_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(archive_original.tempfile, "tempdir", str(tmp_path))
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 45)
    monkeypatch.setattr(archive_original.subprocess, "run", fail)
    with pytest.raises(RuntimeError):
        archive_original.fetch({"rel": "사업/a.pdf", "size": 4, "mtime": 1})
    assert list(tmp_path.iterdir()) == []


def test_fetch_quotes_remote_path(monkeypatch):
    import os
    from pathlib import Path
    def copy(args, **kw):
        assert kw["input"] == "사업/공백 '$(touch nope).pdf".encode() + b"\0"
        assert "StrictHostKeyChecking=yes" in args[2]
        output = Path(args[-1]) / kw["input"][:-1].decode()
        output.parent.mkdir()
        output.write_bytes(b"test")
        os.utime(output, (1, 1))
    monkeypatch.setattr(archive_original.subprocess, "run", copy)
    file = archive_original.fetch({"rel": "사업/공백 '$(touch nope).pdf", "size": 4, "mtime": 1})
    try:
        assert file.read_bytes() == b"test"
    finally:
        file.unlink()
