"""첫 기동의 개발자 계정 — 고정 비밀번호 없이 운영자가 정한 값 또는 무작위 값, 무작위면 바꿀 때까지 설정 화면만."""

from __future__ import annotations

import json
import stat

from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def _app(tmp_path):
    return create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                      processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")


def test_fresh_install_generates_dev_password_and_forces_change(tmp_path, monkeypatch):
    monkeypatch.delenv("ZZAIMY_DEV_PASSWORD", raising=False)
    _app(tmp_path)
    pw_file = tmp_path / "initial_dev_password.txt"
    dev_pw = pw_file.read_text().strip()
    assert len(dev_pw) >= 12 and dev_pw != "devpass" and stat.S_IMODE(pw_file.stat().st_mode) == 0o600
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert data["zzdev"]["must_change"] is True and dev_pw not in (tmp_path / "accounts.json").read_text()
    c = TestClient(_app(tmp_path))                                    # 있는 계정 파일은 그대로 읽는다
    assert c.post("/login", data={"username": "zzdev", "pw": "devpass"}, follow_redirects=False).headers["location"] == "/login?err=1"
    assert c.post("/login", data={"username": "zzdev", "pw": dev_pw}, follow_redirects=False).status_code == 303
    r = c.get("/dev", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings")
    assert c.get("/settings", follow_redirects=False).status_code == 200
    r = c.post("/account/password", data={"current_pw": dev_pw, "new_pw": "new-dev-pass-1", "confirm": "new-dev-pass-1",
                                          "back": "/settings"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert "must_change" not in json.loads((tmp_path / "accounts.json").read_text())["zzdev"]
    assert not c.get("/dev", follow_redirects=False).headers.get("location", "").startswith("/settings")


def test_dev_password_from_env_is_used_without_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DEV_PASSWORD", "chosen-pass-1")
    c = TestClient(_app(tmp_path))
    assert not (tmp_path / "initial_dev_password.txt").exists()
    assert c.post("/login", data={"username": "zzdev", "pw": "chosen-pass-1"}, follow_redirects=False).headers["location"] == "/"
