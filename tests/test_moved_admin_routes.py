"""화면을 따라 옮긴 관리자 처리 주소(10-27) — 옛 주소는 없고, /dev 밖으로 나간 것도 관리자만 한다.

/team/accounts/*·/team/google/* 는 /dev 앞부분 막기(main.check_auth)를 받지 않는다. 처리마다 user_admin.need_admin 이
막는지를 교직원(일반·총괄 책임자)·학생·로그인 안 함·관리자로 하나씩 본다. 막힌 요청은 아무것도 바꾸지 않아야 한다.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app
from zzaimy.ingest import gdrive

MOVED = {
    "/team/accounts/save": {"uid": "newbie", "is_new": "1", "role": "staff", "new_pw": "init-pass-1"},
    "/team/accounts/bulk": {"op": "suspend", "uids": ["kim"]},
    "/team/google/revoke": {"email": "kim@ync.ac.kr"},
    "/team/google/client": {"client_id": "bad", "client_secret": "x"},
}

GONE = [("post", "/dev/users/save"), ("post", "/dev/users/bulk"), ("post", "/dev/google/revoke"), ("post", "/dev/gdrive/client"),
        ("post", "/dev/gdrive/revoke"), ("post", "/dev/collect/make"), ("get", "/dev/collect/peek/helper"),
        ("get", "/dev/collect/file/collect-20261027-000000.zip"), ("get", "/dev/intake/missed.csv"), ("get", "/dev/intake/failed.csv"),
        ("post", "/dev/data/grounded-pull"), ("get", "/dev/data/1.jsonl")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzdev": {"pw": "devpass", "role": "dev"},
        "kim": {"pw": "kim-pass-1", "role": "staff", "dept": "기획처"},
        "boss": {"pw": "boss-pass-1", "role": "staff", "dept": "기획처", "grade": "head"},
        "s1": {"pw": "stu-pass-1", "role": "student"},
    }))
    calls = {"revoke": [], "client": []}
    monkeypatch.setattr(gdrive, "revoke", lambda email: calls["revoke"].append(email))
    real_set_client = gdrive.set_client

    def set_client(cid, secret):
        calls["client"].append(cid)
        return real_set_client(cid, secret)
    monkeypatch.setattr(gdrive, "set_client", set_client)
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), password="boot-pass-1")
    return app, calls


def _as(app, uid=None, pw=None) -> TestClient:
    c = TestClient(app)
    if uid:
        r = c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", uid
    return c


@pytest.mark.parametrize("who", [("kim", "kim-pass-1"), ("boss", "boss-pass-1"), ("s1", "stu-pass-1")])
@pytest.mark.parametrize("path", list(MOVED))
def test_non_admin_is_refused_and_nothing_changes(env, who, path):
    app, calls = env
    c = _as(app, *who)
    before = json.dumps(app.state.accounts, sort_keys=True, default=str)      # 로그인(해시 승격·최근 접속) 뒤 값과 견준다
    r = c.post(path, data=MOVED[path], follow_redirects=False)
    assert r.status_code == 403, (who[0], path, r.status_code)
    assert json.dumps(app.state.accounts, sort_keys=True, default=str) == before
    assert calls == {"revoke": [], "client": []}


@pytest.mark.parametrize("path", list(MOVED))
def test_anonymous_goes_to_login(env, path):
    app, calls = env
    r = _as(app).post(path, data=MOVED[path], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "newbie" not in app.state.accounts and calls == {"revoke": [], "client": []}


def test_admin_does_each_moved_action(env):
    app, calls = env
    dev = _as(app, "zzdev", "devpass")
    r = dev.post("/team/accounts/save", data=MOVED["/team/accounts/save"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/team?") and "ok=" in r.headers["location"]
    assert "newbie" in app.state.accounts
    j = dev.post("/team/accounts/bulk", data=MOVED["/team/accounts/bulk"]).json()
    assert j["ok"] and j["done"] == ["kim"] and app.state.accounts["kim"]["active"] is False
    r = dev.post("/team/google/revoke", data=MOVED["/team/google/revoke"], follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"] and r.headers["location"].endswith("#google-links")
    assert calls["revoke"] == ["kim@ync.ac.kr"]
    r = dev.post("/team/google/client", data=MOVED["/team/google/client"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/team?err=") and calls["client"] == ["bad"]


def test_old_addresses_are_gone(env):
    app, _ = env
    dev = _as(app, "zzdev", "devpass")
    for method, path in GONE:
        assert getattr(dev, method)(path, follow_redirects=False).status_code in (404, 405), path


def test_screens_call_only_new_addresses(env):
    app, _ = env
    dev = _as(app, "zzdev", "devpass")
    team = dev.get("/team").text
    assert 'action="/team/google/client"' in team and 'action="/team/accounts/save"' in team
    collect = dev.get("/dev/train?tab=collect").text
    assert 'action="/dev/train/collect/make"' in collect and "'/dev/train/collect/peek/'" in collect
    for page in (team, collect, dev.get("/dev/flow").text, dev.get("/dev/train").text):
        for old in ("/dev/users/", "/dev/google/", "/dev/gdrive/client", "/dev/gdrive/revoke", "/dev/collect/", "/dev/intake/", "/dev/data/"):
            assert old not in page, old
    js = (__import__("pathlib").Path(__file__).parents[1] / "src/zzaimy/app/static/org-chart.js").read_text(encoding="utf-8")
    assert "'/team/accounts/bulk'" in js and "/dev/users/" not in js
