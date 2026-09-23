"""구글 드라이브 원천(읽기 전용) — 가짜 드라이브 API 로 목록·내보내기·토큰 갱신·원천 등록·허용 경로를 검증한다. 구글에는 아무것도 보내지 않는다."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app
from zzaimy.ingest import gdrive, nas_sync

TREE = {
    "root1": [
        {"id": "f-sub", "name": "2026 공고", "mimeType": gdrive.FOLDER},
        {"id": "d1", "name": "사업 공고", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-09-01T01:02:03.000Z"},
        {"id": "p1", "name": "안내.pdf", "mimeType": "application/pdf", "size": "12", "modifiedTime": "2026-09-02T00:00:00.000Z"},
        {"id": "g1", "name": "그림", "mimeType": "application/vnd.google-apps.drawing"},
        {"id": "h1", "name": ".숨김.pdf", "mimeType": "application/pdf", "size": "1"},
    ],
    "f-sub": [
        {"id": "s1", "name": "예산", "mimeType": "application/vnd.google-apps.spreadsheet", "modifiedTime": "2026-09-03T00:00:00.000Z"},
    ],
}


def fake_drive(calls: list):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600, "refresh_token": "RT", "scope": " ".join(gdrive.SCOPES)})
        if req.url.path.endswith("/userinfo"):
            return httpx.Response(403)                       # 이메일 범위가 없을 때처럼
        if req.url.path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "staff@example.ac.kr"}})
        if req.headers.get("Authorization") != "Bearer AT2":
            return httpx.Response(401)
        path, q = req.url.path, dict(req.url.params)
        if path == "/drive/v3/files":
            parent = q["q"].split("'")[1]
            return httpx.Response(200, json={"files": TREE.get(parent, [])})
        if path.endswith("/export"):
            return httpx.Response(200, content=b"DOCX:" + path.split("/")[-2].encode())
        if path.startswith("/drive/v3/files/"):
            fid = path.split("/")[-1]
            if q.get("alt") == "media":
                return httpx.Response(200, content=b"%PDF-" + fid.encode())
            return httpx.Response(200 if fid in TREE else 404, json={"id": fid, "mimeType": gdrive.FOLDER})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def drive_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ZZAIMY_NAS_AUTO", "0")
    gdrive.set_client("abc.apps.googleusercontent.com", "secret-1")
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps({"staff@example.ac.kr": {
        "refresh_token": "RT", "access_token": "", "expires_at": 0, "scopes": gdrive.SCOPES, "granted_at": "2026-09-22"}}))
    calls: list = []
    monkeypatch.setattr(gdrive, "_http", lambda: fake_drive(calls))
    return tmp_path, calls


def test_backend_lists_exports_and_reads_without_writing(drive_env):
    tmp_path, calls = drive_env
    assert oct((tmp_path / "gdrive_oauth.json").stat().st_mode & 0o777) == "0o600"
    b = gdrive.GDriveBackend("https://drive.google.com/drive/folders/root1?usp=sharing", "staff@example.ac.kr")
    b.check()
    entries = {rel: (size, mtime) for rel, size, mtime in b.walk(True)}
    # 구글 독스·시트는 내보내기 확장자를 붙여 이름 짓고, 그림·숨김 파일은 뺀다
    assert set(entries) == {"사업 공고.docx", "안내.pdf", "2026 공고/예산.xlsx"}
    assert entries["안내.pdf"][0] == 12 and entries["사업 공고.docx"][1] > 0
    assert b.read("사업 공고.docx") == b"DOCX:d1"
    assert b.read("2026 공고/예산.xlsx") == b"DOCX:s1" and b.read("안내.pdf") == b"%PDF-p1"
    dirs, files = b.listdir("")
    assert dirs == [{"name": "2026 공고"}] and [f[0] for f in files] == ["사업 공고.docx", "안내.pdf"]
    assert all(m == "GET" for m, _u in calls if "oauth2" not in _u)          # 드라이브에는 GET 만
    # 만료된 접근 토큰은 갱신 토큰으로 새로 받아 저장한다
    tok = json.loads((tmp_path / "gdrive_tokens.json").read_text())["staff@example.ac.kr"]
    assert tok["access_token"] == "AT2" and tok["expires_at"] > time.time()
    with pytest.raises(ValueError):
        gdrive.GDriveBackend("root1", "nobody@example.ac.kr").check()
    with pytest.raises(ValueError):
        gdrive.folder_id("https://example.com/what?x=1")


def test_source_registration_and_sync_through_nas_path(drive_env, tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    client = TestClient(app)
    r = client.post("/dev/nas/add", data={"name": "드라이브 공고", "backend": "gdrive",
                                          "root": "https://drive.google.com/drive/folders/root1",
                                          "username": "staff@example.ac.kr", "target": "regulation",
                                          "sector": "common", "extensions": "", "recursive": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    src = nas_sync.list_sources()[0]
    assert src["root"] == "root1" and src["backend"] == "gdrive"
    pr = nas_sync.probe(src)
    assert pr["ok"] and pr["n_match"] == 3
    res = nas_sync.sync(src, app.state.db, FakeProcessor(), tmp_path / "inbox")
    assert res["new"] == 3 and res["failed"] == 0
    names = {d["filename"] for d in app.state.db.list_documents("regulation")}
    assert names == {"사업 공고.docx", "안내.pdf", "예산.xlsx"}
    # 계정 없이 등록하면 거절
    r = client.post("/dev/nas/add", data={"name": "x", "backend": "gdrive", "root": "root1", "target": "regulation",
                                          "sector": "common", "recursive": "1"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    page = client.get("/dev/nas")
    assert page.status_code == 200


def test_oauth_flow_stores_tokens_only_on_valid_state(drive_env, tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    client = TestClient(app)
    r = client.get("/dev/gdrive/auth", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(gdrive.AUTH_URL)
    state = dict(httpx.URL(r.headers["location"]).params)["state"]
    assert "drive.readonly" in r.headers["location"] and "access_type=offline" in r.headers["location"]
    bad = client.get("/dev/gdrive/callback", params={"code": "c", "state": "wrong"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    ok = client.get("/dev/gdrive/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "ok=" in ok.headers["location"] and "staff%40example.ac.kr" in ok.headers["location"] or "staff@example.ac.kr" in ok.headers["location"]
    assert "staff@example.ac.kr" in [a["email"] for a in gdrive.list_accounts()]
    client.post("/dev/gdrive/revoke", data={"email": "staff@example.ac.kr"}, follow_redirects=False)
    assert gdrive.list_accounts() == []
    r = client.post("/dev/gdrive/client", data={"client_id": "bad", "client_secret": "x"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_relabel_accounts_replaces_placeholder_with_email(drive_env, tmp_path):
    t = json.loads((tmp_path / "gdrive_tokens.json").read_text())
    t["account-abc123"] = t.pop("staff@example.ac.kr")
    (tmp_path / "gdrive_tokens.json").write_text(json.dumps(t))
    assert gdrive.relabel_accounts() == [("account-abc123", "staff@example.ac.kr")]
    assert [a["email"] for a in gdrive.list_accounts()] == ["staff@example.ac.kr"]

