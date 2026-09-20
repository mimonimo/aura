"""계정 로직 — 해시 저장·본인 비밀번호 변경·관리자 페이지(추가·역할·재설정·비활성·삭제)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def _app(tmp_path, password="boot-pass-1"):
    return create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), password=password,
    )


def _login(c: TestClient, uid: str, pw: str) -> bool:
    r = c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False)
    return r.status_code == 303 and r.headers["location"] == "/"


def test_bootstrap_stores_hashes_not_plaintext(tmp_path):
    _app(tmp_path)
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert set(data) == {"zzaimy", "zzdev"}
    for a in data.values():
        assert "pw" not in a and a["pw_hash"] and a["salt"] and a["active"] is True and a["created_at"]


def test_legacy_plaintext_account_is_upgraded_on_login(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzaimy": {"pw": "old-plain", "role": "staff"},
        "zzdev": {"pw": "devpass", "role": "dev"},
    }))
    c = TestClient(_app(tmp_path))
    assert not _login(c, "zzaimy", "wrong")
    assert _login(c, "zzaimy", "old-plain")
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert "pw" not in data["zzaimy"] and data["zzaimy"]["pw_hash"]   # 평문 → 해시 승격
    assert data["zzdev"].get("pw") == "devpass"                       # 아직 로그인 안 한 계정은 그대로


def test_self_password_change_requires_current_password(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    r = c.post("/account/password", data={"current_pw": "nope", "new_pw": "new-pass-123",
                                          "confirm": "new-pass-123", "back": "/settings"},
               follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/account/password", data={"current_pw": "boot-pass-1", "new_pw": "new-pass-123",
                                          "confirm": "new-pass-123", "back": "/settings"},
               follow_redirects=False)
    assert r.headers["location"].startswith("/settings?ok=")
    c2 = TestClient(_app(tmp_path))
    assert not _login(c2, "zzaimy", "boot-pass-1") and _login(c2, "zzaimy", "new-pass-123")


def test_profile_menu_changes_only_own_account(tmp_path):
    c = TestClient(_app(tmp_path))
    _login(c, "zzaimy", "boot-pass-1")
    page = c.get("/").text
    assert "비밀번호 변경" in page and 'name="target"' not in page   # 다른 계정 선택 라디오 없음
    assert 'action="/account/password"' in page
    assert c.post("/dev/account", data={"target": "zzdev", "new_pw": "x"}).status_code == 404


def test_tool_accounts_live_on_train_page(tmp_path):
    c = TestClient(_app(tmp_path))
    _login(c, "zzaimy", "boot-pass-1")
    assert c.get("/dev/train").status_code == 403           # 담당자는 접근 불가
    # 새로 만든 일괄 저장도 같은 가드를 받는다(전역 /dev 검사)
    assert c.post("/dev/llm/roles", content="role=answer&cid=&model=",
                  headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 403
    d = TestClient(_app(tmp_path))
    _login(d, "zzdev", "devpass")
    page = d.get("/dev/train").text
    assert "toolModal-labelstudio" in page and "로그인 아이디" in page and "비밀번호 변경" in page
    assert "플랫폼 계정" not in page and "계정 추가" not in page   # 시스템 계정 관리는 여기 없다
    assert d.post("/dev/accounts/add", data={"uid": "x1", "new_pw": "abcdefgh", "confirm": "abcdefgh"}).status_code == 404
    r = d.get("/dev/accounts", follow_redirects=False)      # 예전 주소는 모델 학습으로
    assert r.status_code == 301 and r.headers["location"] == "/dev/train"


def test_ls_password_reset_lives_on_train_page(client, monkeypatch):
    from zzaimy.dataset import ls_admin

    monkeypatch.setattr(ls_admin, "available", lambda: True)
    client.app.state.db.set_setting("labelstudio_username", "zzdev@example.org")
    page = client.get("/dev/train").text
    assert "zzdev@example.org" in page and "/dev/train/ls-password" in page
    monkeypatch.setattr(ls_admin, "set_password", lambda pw: (True, "PASS: ok"))
    r = client.post("/dev/train/ls-password", data={"new_pw": "abcdefgh", "confirm": "abcdefgh"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/dev/train?ok=")
    assert client.post("/dev/accounts/ls-password", data={"new_pw": "abcdefgh", "confirm": "abcdefgh"}).status_code == 404


def _unq(s: str) -> str:
    from urllib.parse import unquote

    return unquote(s)


@pytest.fixture
def client(tmp_path):
    return TestClient(_app(tmp_path, password=None))


# ---- ls_admin — 헬퍼 결과 판정은 표준 출력의 PASS/FAIL 로만 (stderr 경고에 속지 않는다) ----

def test_ls_admin_ignores_stderr_warnings(monkeypatch, tmp_path):
    import subprocess

    from zzaimy.dataset import ls_admin

    monkeypatch.setattr(ls_admin, "available", lambda: True)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[:1] == ["systemctl"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        assert kw["env"]["LS_PASSWORD"] == "abcdefgh" and "abcdefgh" not in " ".join(map(str, cmd))
        return subprocess.CompletedProcess(
            cmd, 0, "PASS: Label Studio 사용자 x@y 비밀번호 재설정, 유닛 갱신\n",
            "  warnings.warn(self.APPS_NOT_READY_WARNING_MSG, category=RuntimeWarning)\n")

    monkeypatch.setattr(ls_admin.subprocess, "run", fake_run)
    ok, msg = ls_admin.set_password("abcdefgh")
    assert ok and msg.startswith("PASS")                    # 경고는 실패가 아니다

    def fake_fail(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 1, "", "Traceback (most recent call last):\n  ...\nOperationalError: database is locked\n")

    monkeypatch.setattr(ls_admin.subprocess, "run", fake_fail)
    ok, msg = ls_admin.set_password("abcdefgh")
    assert not ok and "database is locked" in msg            # 실패면 마지막 실질 오류 줄

    def fake_helper_fail(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, "FAIL: Label Studio 사용자 x 이(가) 없습니다\n", "")

    monkeypatch.setattr(ls_admin.subprocess, "run", fake_helper_fail)
    ok, msg = ls_admin.set_password("abcdefgh")
    assert not ok and msg.startswith("FAIL")
