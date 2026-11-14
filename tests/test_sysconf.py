"""서버 설정을 화면에서(11-15) — 화면 값이 서버 파일 값을 덮고, 되돌리면 서버 파일 값으로. 비밀 값은 다시 보이지 않는다."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from zzaimy.app import sysconf
    monkeypatch.setenv("ZZAIMY_RERANK_URL", "http://file-host:8015/score")    # 서버 파일 값
    monkeypatch.delenv("ZZAIMY_EMBED_URL", raising=False)
    monkeypatch.delenv("ZZAIMY_GOOGLE_CSE_KEY", raising=False)
    monkeypatch.setattr(sysconf, "_orig", {})
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    yield TestClient(app)
    for k in ("ZZAIMY_EMBED_URL", "ZZAIMY_GOOGLE_CSE_KEY"):
        os.environ.pop(k, None)


def test_screen_value_overrides_file_and_reset_restores(client):
    r = client.post("/dev/sysconf/save", data={"key": "ZZAIMY_RERANK_URL", "value": "http://screen-host:9000/score"},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert os.environ["ZZAIMY_RERANK_URL"] == "http://screen-host:9000/score"       # 곧바로 프로세스에
    page = client.get("/dev/tools").text
    assert "서버 설정" in page and "screen-host" in page and "화면 설정" in page
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_RERANK_URL", "reset": "1"})
    assert os.environ["ZZAIMY_RERANK_URL"] == "http://file-host:8015/score"         # 서버 파일 값으로


def test_new_value_and_bad_values(client):
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_EMBED_URL", "value": "http://emb:8016/embed"})
    assert os.environ["ZZAIMY_EMBED_URL"] == "http://emb:8016/embed"
    r = client.post("/dev/sysconf/save", data={"key": "ZZAIMY_EMBED_URL", "value": "emb:8016"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and os.environ["ZZAIMY_EMBED_URL"] == "http://emb:8016/embed"
    r = client.post("/dev/sysconf/save", data={"key": "ZZAIMY_PASSWORD", "value": "x"}, follow_redirects=False)
    assert "err=" in r.headers["location"]                                          # 목록 밖 이름은 받지 않는다
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_EMBED_URL", "reset": "1"})
    assert "ZZAIMY_EMBED_URL" not in os.environ                                     # 서버 파일에 없던 값은 지워진다


def test_secret_is_masked_and_blank_keeps_it(client):
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_GOOGLE_CSE_KEY", "value": "abcdefgh1234"})
    page = client.get("/dev/tools").text
    assert "abcdefgh1234" not in page and "1234" in page
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_GOOGLE_CSE_KEY", "value": ""})
    assert os.environ["ZZAIMY_GOOGLE_CSE_KEY"] == "abcdefgh1234"


def test_saved_values_apply_on_next_start(tmp_path, monkeypatch):
    from zzaimy.app import sysconf
    from zzaimy.app.db import Database
    monkeypatch.delenv("ZZAIMY_EMBED_URL", raising=False)
    monkeypatch.setattr(sysconf, "_orig", {})
    db = Database(tmp_path / "t.db")
    db.set_setting("env:ZZAIMY_EMBED_URL", "http://emb:1/embed")
    db.set_setting("env:ZZAIMY_PASSWORD", "nope")                                   # 목록 밖은 넣지 않는다
    assert sysconf.apply_all(db) == 1
    assert os.environ["ZZAIMY_EMBED_URL"] == "http://emb:1/embed" and os.environ.get("ZZAIMY_PASSWORD") != "nope"
    os.environ.pop("ZZAIMY_EMBED_URL", None)


def test_check_reports_search_service(client, monkeypatch):
    from zzaimy.app import search_serving
    monkeypatch.setattr(search_serving, "_health", lambda url: {"ok": True, "model": "rerank-v1", "detail": ""})
    j = client.post("/dev/sysconf/check", data={"key": "ZZAIMY_RERANK_URL"}).json()
    assert j["ok"] and "rerank-v1" in j["text"]


def test_archive_source_accepts_folder_or_ssh_and_checks_folder(client, tmp_path):
    for bad in ("relative/path", "/a/../b", "host:path"):
        r = client.post("/dev/sysconf/save", data={"key": "ZZAIMY_ARCHIVE_SOURCE", "value": bad}, follow_redirects=False)
        assert "err=" in r.headers["location"], bad
    (tmp_path / "src" / "기획처").mkdir(parents=True)
    (tmp_path / "src" / "기획처" / "a.hwpx").write_bytes(b"x")
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_ARCHIVE_SOURCE", "value": str(tmp_path / "src")})
    j = client.post("/dev/sysconf/check", data={"key": "ZZAIMY_ARCHIVE_SOURCE"}).json()
    assert j["ok"] and "폴더 확인" in j["text"]
    client.post("/dev/sysconf/save", data={"key": "ZZAIMY_ARCHIVE_SOURCE", "reset": "1"})
    os.environ.pop("ZZAIMY_ARCHIVE_SOURCE", None)
