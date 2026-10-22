"""공용 디렉터리 창 경로 — 전체 찾기는 쓸 수 있는 부서 폴더만, 드라이브 미연결이면 부서 결과만."""
from __future__ import annotations

from fastapi.testclient import TestClient

from zzaimy.app import webhwp, webhwp_drive
from zzaimy.app.drafter import SliceDrafter
from zzaimy.app.main import create_app
from zzaimy.app.pipeline import DocumentProcessor


def _app(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=DocumentProcessor(),
                     drafter=SliceDrafter(), password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"}, follow_redirects=False).status_code == 303
    return app, c


def test_search_finds_dept_files_with_location(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    base = __import__("pathlib").Path(app.state.data_dir)
    d = webhwp.folder_dir(base, webhwp.COMMON, "작업")
    d.mkdir(parents=True, exist_ok=True)
    (d / "AI 교육 계획서.hwpx").write_bytes(b"x")
    db.add_file("webhwp", str(d / "AI 교육 계획서.hwpx"), name="AI 교육 계획서.hwpx", size=1)
    j = c.get("/api/finder/search", params={"q": "교육 계획"}).json()
    assert j["drive_state"] in ("no-account", "ok", "fail")
    assert any(x["name"].startswith("AI 교육") and x["dept"] == webhwp.COMMON and x["folder"] == "작업" for x in j["dept"])
    assert c.get("/api/finder/search", params={"q": ""}).json()["dept"] == []


def test_drive_needs_account_and_accept_filter(tmp_path):
    _, c = _app(tmp_path)
    assert c.get("/api/finder/drive").status_code == 409
    docs = [{"name": "a.pdf"}, {"name": "b.hwpx"}, {"name": "c.png"},
            {"name": "기획안", "mimeType": "application/vnd.google-apps.document"}]
    assert [f["name"] for f in docs if webhwp_drive.accepts(f, "docs")] == ["a.pdf", "b.hwpx", "기획안"]
    assert [f["name"] for f in docs if webhwp_drive.accepts(f, "hwp")] == ["b.hwpx"]


def test_chat_page_loads_shared_finder(tmp_path):
    _, c = _app(tmp_path)
    page = c.get("/chat").text
    assert "/static/aura-finder.js" in page


def test_quick_search_lists_titles_by_kind(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    pid = db.create_project("grant", "AI 교육 혁신 사업", owner="zzaimy")
    j = c.get("/api/quick-search", params={"q": "교육 혁신"}).json()
    assert any(p["id"] == pid for p in j["projects"]) and set(j) >= {"chats", "projects", "docs", "hwp"}
    empty = c.get("/api/quick-search").json()
    assert empty["recent"] and any(p["id"] == pid for p in empty["projects"])      # 말이 없으면 최근·내 프로젝트
    page = c.get("/chat").text
    assert "data-quick-search" in page and "/static/quick-search.js" in page
