"""부서 드라이브를 부서 쪽에서 연결(10-23) — 부서가 함께 쓸 구글 계정으로 로그인, 권한은 총괄 책임자·관리자, 관리자 화면은 확인용,
부서 표 동작의 JSON 응답. 실제 구글에는 가지 않는다(가짜 메일은 example.ync.ac.kr)."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.ingest import dept_drive, gdocs, gdrive, gdrive_files

DOM = "example.ync.ac.kr"
J = {"Accept": "application/json"}


def _fake_google(monkeypatch, emails=(f"office@{DOM}", f"kim@{DOM}")):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [{"email": e, "granted_at": "2026-10-23 09:00", "scopes": []} for e in emails])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)
    monkeypatch.setattr(gdrive, "client_config", lambda: {"client_id": "test-client", "client_secret": "x", "domain": DOM})
    monkeypatch.setattr(gdrive, "revoke", lambda email: None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    app = _app(tmp_path)
    dev = TestClient(app)
    assert _login(dev, "zzdev", "devpass")
    for n in ("기획처", "기획팀", "학생처"):
        assert dev.post("/org/create", json={"name": n}).json()["ok"]
    app.state.accounts["zzaimy"].update(dept="기획처", grade="lead")
    staff = TestClient(app)
    assert _login(staff, "zzaimy", "boot-pass-1")
    return app, dev, staff


def test_only_head_or_admin_connects(env):
    app, dev, staff = env
    assert staff.get("/team/drive", params={"dept": "기획처"}).json()["can_manage"] is False      # 사업 책임자는 보기만
    assert staff.post("/team/drive/connect", data={"dept": "기획처"}, follow_redirects=False).status_code == 403
    assert staff.get("/team/drive", params={"dept": "학생처"}).status_code == 403                  # 다른 부서는 못 본다
    app.state.accounts["zzaimy"]["grade"] = "head"
    r = staff.post("/team/drive/connect", data={"dept": "기획처"}, follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["prompt"] == ["select_account consent"] and q["hd"] == [DOM]
    assert gdrive.state_purpose(q["state"][0]) == "dept:기획처"
    assert staff.post("/team/drive/connect", data={"dept": "학생처"}, follow_redirects=False).status_code == 403
    assert dev.post("/team/drive/connect", data={"dept": "학생처"}, follow_redirects=False).status_code == 303


def test_callback_binds_department_not_person(env, monkeypatch):
    app, dev, staff = env
    app.state.accounts["zzaimy"]["grade"] = "head"
    db = app.state.db
    made = []

    def fake_exchange(code, state, uri, user="", purpose=None, http=None):
        assert purpose == "dept:기획처" and user == "zzaimy"
        return f"office@{DOM}"
    monkeypatch.setattr(gdrive, "exchange_code", fake_exchange)

    def fake_connect(db_, dept, account, parent="root", http=None, shared=False, actor=""):
        made.append((dept, account, parent, shared, actor))
        for k, v in (("google_root", "FOLDER1"), ("google_root_by", account), ("google_account_dept", account),
                     ("google_root_shared", "1" if shared else ""), ("google_root_actor", actor)):
            db_.set_setting(f"{k}:{dept}", v)
        return "FOLDER1"
    monkeypatch.setattr(dept_drive, "connect", fake_connect)
    monkeypatch.setattr(dept_drive, "sync", lambda *a, **k: {"added": 1, "removed": 0, "error": ""})
    r = staff.post("/team/drive/connect", data={"dept": "기획처", "back": "/team?dept=기획처"}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = staff.get(f"/dev/gdrive/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/team?dept=") and "ok=" in r.headers["location"]
    assert made == [("기획처", f"office@{DOM}", "root", True, "zzaimy")]
    assert db.get_setting("google_account:zzaimy", "") == ""                    # 연결한 사람 본인 계정에는 묶지 않는다
    s = staff.get("/team/drive", params={"dept": "기획처"}).json()
    assert s["connected"] and s["shared"] and s["account"] == f"office@{DOM}" and s["can_manage"]
    # 부서 폴더 안 문서는 부서 계정으로(본인 연결이 있어도) — 연결 전 부서는 본인 드라이브
    db.set_setting("google_account:zzaimy", f"kim@{DOM}")
    assert gdrive_files.doc_account_for(db, "zzaimy", "기획처") == f"office@{DOM}"
    assert gdrive_files.doc_account_for(db, "zzaimy", "학생처") == f"kim@{DOM}"
    # 둘러보기·가져오기(부서 폴더 밖)는 본인 계정만 — 부서 계정 토큰을 쓰지 않는다
    assert gdrive_files.account_for(db, "zzaimy", "기획처") == f"kim@{DOM}"


def test_exchange_rejects_purpose_mismatch(monkeypatch):
    monkeypatch.setattr(gdrive, "client_config", lambda: {"client_id": "test-client", "client_secret": "x"})
    monkeypatch.setattr(gdrive, "configured", lambda: True)
    url = gdrive.auth_url("https://aura.example/cb", "u1", purpose="dept:기획처", select_account=True)
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(ValueError):
        gdrive.exchange_code("c", state, "https://aura.example/cb", user="u1", purpose="")


def test_admin_table_json_actions(env, monkeypatch):
    app, dev, staff = env
    db = app.state.db
    r = dev.post("/org/move", json={"dept": "기획팀", "parent": "기획처"})
    assert r.status_code == 200 and r.json()["ok"]
    nodes = {n["name"]: n for n in dev.get("/api/org").json()["nodes"]}
    assert nodes["기획팀"]["parent"] == "기획처"                                # 조직 관리 트리(ADR-0073)
    r = dev.post("/org/move", json={"dept": "기획처", "parent": "없는부서"})
    assert r.status_code == 400 and not r.json()["ok"]
    r = dev.post("/org/create", json={"name": "새부서"})
    assert r.json()["ok"] and r.json()["name"] == "새부서"
    assert dev.post("/org/delete", json={"dept": "새부서"}).json()["ok"]
    r = dev.post("/org/delete", json={"dept": "없는부서"})
    assert r.status_code == 400 and not r.json()["ok"]
    # 비상 연결 끊기 — 준 권한을 거두고 설정을 비운다
    for k, v in (("google_root", "FOLDER1"), ("google_root_by", f"office@{DOM}"), ("google_account_dept", f"office@{DOM}"),
                 ("google_root_shared", "1"), ("gdept", json.dumps({f"kim@{DOM}": "P1"}))):
        db.set_setting(f"{k}:학생처", v)
    deleted = []
    monkeypatch.setattr(dept_drive, "_delete", lambda by, folder, pid, http: deleted.append(pid) or True)
    r = dev.post("/team/drive/disconnect", data={"dept": "학생처"}, headers=J)
    assert r.json()["ok"] and deleted == ["P1"] and db.get_setting("google_root:학생처") == ""
    assert db.get_setting("google_account_dept:학생처") == ""
    # 부서 구성원 — 조직 관리의 구성원 탭 자료
    people = dev.get("/api/org/members", params={"dept": "기획처"}).json()["rows"]
    assert people and people[0]["uid"] == "zzaimy" and people[0]["primary"] and people[0]["grade_label"] == "사업 책임자"


def test_connect_reuses_folder_for_same_account(monkeypatch):
    class DB(dict):
        def get_setting(self, k, d=""):
            return self.get(k, d)

        def set_setting(self, k, v):
            self[k] = v
    db = DB({"google_root:기획처": "OLD", "google_root_by:기획처": f"office@{DOM}"})
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: {})
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(200, json={"id": "OLD", "trashed": False})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert dept_drive.connect(db, "기획처", f"office@{DOM}", http=http, shared=True, actor="u1") == "OLD"
    assert calls == [("GET", "/drive/v3/files/OLD")] and db["google_root_shared:기획처"] == "1"


def test_dept_token_not_used_outside_dept_folder(env, monkeypatch):
    """보안 검토: 부서가 연결한 계정 토큰은 부서 폴더 안 문서에만 — 둘러보기·가져오기·계정 고르기에는 본인 계정만."""
    app, dev, staff = env
    db = app.state.db
    for k, v in (("google_root", "FOLDER1"), ("google_root_by", f"office@{DOM}"), ("google_account_dept", f"office@{DOM}"),
                 ("google_root_shared", "1")):
        db.set_setting(f"{k}:기획처", v)
    # 본인 연결이 없는 부서 사람 — 부서 계정으로 대신하지 않는다
    assert gdrive_files.account_for(db, "zzaimy", "기획처") == ""
    assert gdrive_files.usable_accounts(db, "zzaimy", "기획처", "staff") == set()
    assert gdrive_files.doc_account_for(db, "zzaimy", "기획처") == f"office@{DOM}"
    # 파인더 드라이브 둘러보기 — 부서 계정 드라이브 전체가 보이지 않는다
    from zzaimy.app import webhwp_drive
    seen = []
    monkeypatch.setattr(webhwp_drive, "browse", lambda email, *a, **k: seen.append(email) or {"items": []})
    monkeypatch.setattr(webhwp_drive, "drives", lambda email: [])
    assert staff.get("/api/finder/drive").status_code == 409 and seen == []
    # 본인 계정을 이으면 본인 계정으로
    db.set_setting("google_account:zzaimy", f"kim@{DOM}")
    assert staff.get("/api/finder/drive").status_code == 200 and seen == [f"kim@{DOM}"]
    assert gdrive_files.usable_accounts(db, "zzaimy", "기획처", "staff") == {f"kim@{DOM}"}
    # 예전 방식(관리자가 정한 부서 공용 계정, shared 아님)은 그대로
    db.set_setting("google_account_dept:학생처", f"office@{DOM}")
    assert gdrive_files.account_for(db, "park", "학생처") == f"office@{DOM}"


def test_callback_rejects_other_user_and_consumes_state(env, monkeypatch):
    """한 사람이 시작한 부서 연결의 되돌아오는 주소를 다른 사람(관리자 포함)이 열어도 부서에 잇지 않는다. 권한 없이 돌아온 state 는 버린다."""
    app, dev, staff = env
    app.state.accounts["zzaimy"]["grade"] = "head"
    made = []
    monkeypatch.setattr(dept_drive, "connect", lambda *a, **k: made.append(a) or "F")
    posted = []

    class NoHttp:
        def post(self, *a, **k):
            posted.append(a)
            raise AssertionError("토큰 교환까지 가면 안 된다")
    monkeypatch.setattr(gdrive, "_http", lambda: NoHttp())
    r = staff.post("/team/drive/connect", data={"dept": "기획처"}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = dev.get(f"/dev/gdrive/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert made == [] and posted == [] and gdrive.state_purpose(state) == ""        # 한 번 쓰면 끝
    # 권한이 없어진 뒤 돌아오면 state 도 버린다
    r = staff.post("/team/drive/connect", data={"dept": "기획처"}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    app.state.accounts["zzaimy"]["grade"] = "lead"
    r = staff.get(f"/dev/gdrive/callback?code=c&state={state}", follow_redirects=False)
    assert "err=" in r.headers["location"] and made == [] and gdrive.state_purpose(state) == ""


def test_connect_failure_drops_unbound_token(env, monkeypatch):
    app, dev, staff = env
    app.state.accounts["zzaimy"]["grade"] = "head"
    monkeypatch.setattr(gdrive, "exchange_code", lambda *a, **k: f"new@{DOM}")

    def boom(*a, **k):
        raise PermissionError("부서 폴더를 만들지 못했습니다(403)")
    monkeypatch.setattr(dept_drive, "connect", boom)
    revoked = []
    monkeypatch.setattr(gdrive, "revoke", lambda email: revoked.append(email))
    r = staff.post("/team/drive/connect", data={"dept": "기획처"}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = staff.get(f"/dev/gdrive/callback?code=c&state={state}", follow_redirects=False)
    assert "err=" in r.headers["location"] and revoked == [f"new@{DOM}"]
