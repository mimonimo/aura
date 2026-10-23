"""잠금 — 계정 쓰기(내 비밀번호 바꾸기·평문 승격)가 조직·계정 공용 잠금(org_lock) 안에서 도는가,
사업 관리 줄 저장이 읽은 판과 지금 판을 견주어 다른 사람이 먼저 고쳤으면 409 로 거절하는가. 사람·사업은 모두 가짜 값."""
from __future__ import annotations

import json
import re
import threading
import time
from html import unescape

from tests.test_accounts import _app, _login
from tests.test_program_admin_flow import KEY, filled  # noqa: F401  (고정물)
from tests.test_program_sheet import LEDGER, world  # noqa: F401  (고정물)
from tests.test_staff_grades import _no_google  # noqa: F401  (구글 끄기 고정물)
from fastapi.testclient import TestClient
from zzaimy.app import org_lock, program_admin

PW, NEW = "pass-12345678", "new-pass-87654321"


def _held_while(fn, wait: float = 0.4):
    """공용 잠금을 다른 스레드에서 쥔 채 fn 을 돌린다 — fn 이 잠금을 기다리면 잠금을 놓기 전에는 끝나지 않는다."""
    got, done = {}, threading.Event()
    org_lock.LOCK.acquire()
    try:
        t = threading.Thread(target=lambda: (got.setdefault("r", fn()), done.set()))
        t.start()
        time.sleep(wait)
        finished_early = done.is_set()
    finally:
        org_lock.LOCK.release()
    t.join(10)
    return finished_early, got.get("r")


def test_password_change_waits_for_account_lock(tmp_path):
    app = _app(tmp_path)
    app.state.accounts["kim"] = app.state.new_account("kim", PW, "staff")
    c = TestClient(app)
    assert _login(c, "kim", PW)
    early, r = _held_while(lambda: c.post("/account/password", data={"current_pw": PW, "new_pw": NEW}, follow_redirects=False))
    assert not early and "ok=" in r.headers["location"]
    saved = json.loads((tmp_path / "accounts.json").read_text())
    assert saved["kim"]["pw_hash"] and not (tmp_path / "accounts.tmp").exists()
    assert _login(TestClient(app), "kim", NEW)


def test_plaintext_upgrade_at_login_waits_for_lock(tmp_path):
    app = _app(tmp_path)
    app.state.accounts["old"] = {"role": "staff", "name": "", "active": True, "pw": PW}
    early, ok = _held_while(lambda: _login(TestClient(app), "old", PW))
    assert not early and ok
    saved = json.loads((tmp_path / "accounts.json").read_text())
    assert "pw" not in saved["old"] and saved["old"]["pw_hash"]


def _ver(page: str, key: str) -> str:
    for m in re.finditer(r"data-edit='([^']*)'", page):
        d = json.loads(unescape(m.group(1)))
        if d.get("key") == key:
            return d["ver"]
    raise AssertionError("줄을 찾지 못함")


def test_program_save_rejects_stale_version(filled):
    app, tmp_path, dev, *_ = filled
    v0 = _ver(dev.get("/programs/admin", params={"tab": "all"}).text, KEY)
    assert v0 == program_admin.row_version(program_admin.load_store(tmp_path), KEY)
    form = {"key": KEY, "name": "가나다 혁신사업", "dept": "기획처"}
    r = dev.post("/programs/admin/save", data={**form, "l3": "먼저 고친 값", "ver": v0}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    # 같은 판을 들고 있던 다른 창 — 덮지 않고 409
    r = dev.post("/programs/admin/save", data={**form, "l3": "나중 값", "ver": v0}, follow_redirects=False)
    assert r.status_code == 409 and r.json()["detail"] == program_admin.STALE
    rows = {x["key"]: x for x in program_admin.merged_rows(tmp_path)}
    assert rows[KEY]["l3"] == "먼저 고친 값"
    # 새로 불러온 판으로는 저장된다
    v1 = _ver(dev.get("/programs/admin", params={"tab": "all"}).text, KEY)
    assert v1 != v0
    r = dev.post("/programs/admin/save", data={**form, "l3": "나중 값", "ver": v1}, follow_redirects=False)
    assert "ok=" in r.headers["location"]


def test_program_save_waits_for_lock_and_ui_keeps_values(filled):
    app, tmp_path, dev, *_ = filled
    early, r = _held_while(lambda: dev.post("/programs/admin/save", data={"key": KEY, "name": "가나다 혁신사업", "dept": "기획처",
                                                                          "l3": "잠금 뒤"}, follow_redirects=False))
    assert not early and "ok=" in r.headers["location"]
    page = dev.get("/programs/admin", params={"tab": "all"}).text
    assert 'name="ver"' in page and "res.status === 409" in page
