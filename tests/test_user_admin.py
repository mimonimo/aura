"""사용자 관리(/dev/users)와 사용자별 구글 계정 연결 — 관리자만, 자기 잠금 방지, 도메인 제한, 미연결 사용자 대체 설정."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.ingest import gdocs, gdrive


def _fake_google(monkeypatch, emails=("kim@ync.ac.kr", "dev@ync.ac.kr"), revoked=None):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [{"email": e, "granted_at": "2026-09-30 09:00", "scopes": []} for e in emails])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)
    monkeypatch.setattr(gdrive, "revoke", lambda email: (revoked if revoked is not None else []).append(email))


def _dev(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzdev", "devpass")
    return c


def test_staff_cannot_open_user_admin(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    assert c.get("/dev/users", follow_redirects=False).status_code == 403
    assert c.post("/dev/users/save", data={"uid": "x", "is_new": "1"}, follow_redirects=False).status_code == 403


def test_dev_creates_user_and_new_user_can_log_in(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    c = _dev(tmp_path)
    page = c.get("/dev/users").text
    assert "사용자 관리" in page and "zzaimy" in page and "허용 도메인 ync.ac.kr" in page
    r = c.post("/dev/users/save", data={"uid": "Kim_ops", "is_new": "1", "name": "김담당", "role": "staff", "dept": "산학협력단",
                                        "new_pw": "init-pass-1", "google": "kim@ync.ac.kr"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert data["kim_ops"]["dept"] == "산학협력단" and data["kim_ops"]["role"] == "staff" and "pw" not in data["kim_ops"]
    assert c.app.state.db.get_setting("google_account:kim_ops") == "kim@ync.ac.kr"
    c2 = TestClient(c.app)
    assert _login(c2, "kim_ops", "init-pass-1")
    status = c2.get("/account/google").json()
    assert status["bound"] == "kim@ync.ac.kr" and status["bound_ok"] and status["via"] == "본인"
    assert "내 구글 계정" in c2.get("/settings").text


def test_user_save_rejects_bad_input_and_self_lockout(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    c = _dev(tmp_path)

    def err(data):
        r = c.post("/dev/users/save", data=data, follow_redirects=False)
        return "err=" in r.headers["location"]
    assert err({"uid": "ab", "is_new": "1", "role": "staff", "new_pw": "init-pass-1"})             # 아이디 짧음
    assert err({"uid": "kim.ops", "is_new": "1", "role": "staff", "new_pw": "init-pass-1"})        # 점(세션 토큰 구분자)
    assert err({"uid": "zzaimy", "is_new": "1", "role": "staff", "new_pw": "init-pass-1"})         # 중복
    assert err({"uid": "newbie", "is_new": "1", "role": "staff", "new_pw": "short"})               # 비밀번호 짧음
    assert err({"uid": "newbie", "is_new": "1", "role": "staff", "new_pw": "init-pass-1", "google": "x@gmail.com"})   # 미연결
    assert err({"uid": "zzdev", "role": "staff", "active": "1"})                                   # 스스로 강등
    assert err({"uid": "zzdev", "role": "dev"})                                                    # 스스로 끄기
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert "newbie" not in data and data["zzdev"]["role"] == "dev"


def test_deactivate_and_reset_password(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    c = _dev(tmp_path)
    c.post("/dev/users/save", data={"uid": "zzaimy", "role": "staff", "new_pw": "reset-pass-9", "active": "1"})
    c2 = TestClient(c.app)
    assert _login(c2, "zzaimy", "reset-pass-9")
    c.post("/dev/users/save", data={"uid": "zzaimy", "role": "staff"})                            # 사용 끔
    assert c2.get("/settings", follow_redirects=False).status_code in (303, 401)               # 세션 즉시 무효
    assert not _login(TestClient(c.app), "zzaimy", "reset-pass-9")
    c.post("/dev/users/save", data={"uid": "zzaimy", "role": "staff", "active": "1"})
    assert _login(TestClient(c.app), "zzaimy", "reset-pass-9")


def test_account_status_ui_new_edit_and_reopen(tmp_path, monkeypatch):
    import re
    import shutil
    import subprocess
    import pytest

    _fake_google(monkeypatch)
    page = _dev(tmp_path).get("/dev/users").text
    assert 'data-account-status hidden' in page
    assert 'name="suspended"' in page and '계정 사용 중지' in page
    assert '계정과 기존 자료는 삭제되지 않습니다.' in page
    assert 'name="active" value="1" checked' not in page
    node = shutil.which("node")
    if not node:
        pytest.skip("JavaScript runtime unavailable")
    script = next(s for s in re.findall(r'<script[^>]*>(.*?)</script>', page, re.S) if 'syncAccountStatus' in s)
    harness = r'''
const assert = require('node:assert/strict');
const control = () => ({value:'', checked:false, addEventListener(k, fn) { this[k] = fn; }});
const f = {elements:{}, addEventListener(k, fn) { this[k] = fn; }};
for (const key of ['is_new','uid','name','role','dept','google','active','suspended','new_pw']) f.elements[key] = f[key] = control();
let isNew = true;
const btn = {dataset:{user:'worker',role:'staff',active:'1'}, hasAttribute() {return isNew;}, addEventListener(k, fn) {this[k] = fn;}};
const status = {hidden:true}, title = {}, save = {}, pw = {};
global.document = {
 querySelector(s) { return s.endsWith(' form') ? f : s.includes('data-account-status') ? status : s.includes('data-save-user') ? save : s.includes('data-pw-label') ? pw : title; },
 querySelectorAll(s) { return s === '[data-modal-open="#userEdit"]' ? [btn] : []; }
};
'''
    checks = r'''
btn.click(); assert.equal(status.hidden, true); assert.equal(f.suspended.disabled, true); assert.equal(f.active.value, '1'); assert.equal(save.textContent, '사용자 추가');
isNew = false; btn.click(); assert.equal(status.hidden, false); assert.equal(f.suspended.checked, false); assert.equal(f.suspended.disabled, false);
f.suspended.checked = true; f.suspended.change(); assert.equal(f.active.value, '');
btn.dataset.active = ''; btn.click(); assert.equal(f.suspended.checked, true);
f.suspended.checked = false; f.submit(); assert.equal(f.active.value, '1');
isNew = true; btn.click(); assert.equal(status.hidden, true); assert.equal(f.suspended.checked, false); assert.equal(f.active.value, '1');
'''
    subprocess.run([node, '-e', harness + script + checks], check=True, capture_output=True, text=True)


def test_unconnected_user_has_no_writing_account_and_domain_setting(tmp_path, monkeypatch):
    revoked = []
    _fake_google(monkeypatch, emails=("dev@ync.ac.kr",), revoked=revoked)
    c = _dev(tmp_path)
    db = c.app.state.db
    # 허용 계정이 하나뿐이어도 연결하지 않은 사용자에게 빌려주지 않는다 — 문서 작성은 각자 연결한 학교 계정으로
    c2 = TestClient(c.app)
    _login(c2, "zzaimy", "boot-pass-1")
    assert c2.get("/account/google").json()["effective"] == ""
    assert "본인 연결 필요" in c.get("/dev/users").text
    c.post("/dev/google/settings", data={"domain": "ync.ac.kr"})
    assert gdrive.allowed_domain() == "ync.ac.kr"
    assert "err=" in c.post("/dev/google/settings", data={"domain": "not a domain"}, follow_redirects=False).headers["location"]
    # 부서 공용 계정·공유 드라이브(주소에서 id 를 뽑는다)
    c.post("/dev/google/dept", data={"dept": "산학협력단", "account": "dev@ync.ac.kr",
                                     "root": "https://drive.google.com/drive/folders/0AbCdEfGhIjKlMnOp?usp=sharing"})
    assert db.get_setting("google_account_dept:산학협력단") == "dev@ync.ac.kr" and db.get_setting("google_root:산학협력단") == "0AbCdEfGhIjKlMnOp"
    # 연결 삭제는 토큰과 사용자·부서 이음을 함께 푼다
    db.set_setting("google_account:zzaimy", "dev@ync.ac.kr")
    c.post("/dev/google/revoke", data={"email": "dev@ync.ac.kr"})
    assert revoked == ["dev@ync.ac.kr"] and db.get_setting("google_account:zzaimy") == "" and db.get_setting("google_account_dept:산학협력단") == ""


def test_callback_is_open_to_staff_and_returns_to_settings(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    monkeypatch.setattr(gdrive, "exchange_code", lambda code, state, uri: "kim@ync.ac.kr")
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    r = c.get("/dev/gdrive/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings?ok=")
    assert c.app.state.db.get_setting("google_account:zzaimy") == "kim@ync.ac.kr"
    assert c.get("/dev/nas", follow_redirects=False).status_code == 403                      # 다른 /dev 는 여전히 막힘


def test_exchange_code_rejects_other_domain(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setattr(gdrive, "client_config", lambda: {"client_id": "a.apps.googleusercontent.com", "client_secret": "s", "domain": "ync.ac.kr"})
    gdrive._pending_states["st"] = __import__("time").time()
    posts = []

    def handler(req: httpx.Request) -> httpx.Response:
        posts.append(str(req.url))
        if "token" in req.url.path and "revoke" not in req.url.path:
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
        if "revoke" in req.url.path:
            return httpx.Response(200)
        return httpx.Response(200, json={"user": {"emailAddress": "someone@gmail.com"}})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    import pytest
    with pytest.raises(ValueError, match="ync.ac.kr 계정만"):
        gdrive.exchange_code("code", "st", "https://x/cb", http=http)
    assert any("revoke" in u for u in posts)
    assert "hd=ync.ac.kr" in gdrive.auth_url("https://x/cb")
