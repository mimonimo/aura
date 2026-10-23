"""문서함(/hwp) 화면 — 즐겨찾기·최근 폴더(사람마다), 기안기 판 숨김, 그 자리 지우기, 복사해 쓰기(곳 고르기·프로젝트),
새 폴더·폴더 이름 바꾸기 권한(공통 안 됨), 「내 폴더」(본인·관리자만), 편집 잠금의 서버 검사, 드라이브 왼쪽 목록."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _hwpx, _setup, _staff, _up
from zzaimy.app import webhwp

PW = "pass-12345678"
J = {"Accept": "application/json"}


def _side(page: str) -> str:
    """문서함 왼쪽 위치 목록만."""
    return page.split('aria-label="위치"', 1)[1].split("</nav>", 1)[0]


def _two(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", PW) and _login(lee, "lee", PW)
    return app, kim, lee


def test_favorites_are_per_user_and_only_for_reachable_folders(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "2026 공문"})
    r = kim.post("/hwp/favorite", data={"dept": "기획처", "folder": "2026 공문", "on": "1"}, headers=J)
    assert r.status_code == 200 and r.json()["on"] is True
    assert [x["name"] for x in r.json()["favorites"]] == ["2026 공문"]
    page = kim.get("/hwp?dept=기획처").text
    assert "즐겨찾기" in page and 'data-place="fav"' in page and "solar:star-bold" in page
    # 남의 부서 폴더·없는 폴더·부서 밖 경로는 넣을 수 없다
    assert lee.post("/hwp/favorite", data={"dept": "기획처", "folder": "2026 공문", "on": "1"}, headers=J).status_code == 403
    assert kim.post("/hwp/favorite", data={"dept": "기획처", "folder": "없는 폴더", "on": "1"}, headers=J).status_code == 403
    assert kim.post("/hwp/favorite", data={"dept": "기획처", "folder": "../산학협력단", "on": "1"}, headers=J).status_code == 400
    assert 'data-place="fav"' not in lee.get("/hwp").text                              # 사람마다 따로
    r = kim.post("/hwp/favorite", data={"dept": "기획처", "folder": "2026 공문", "on": "0"}, headers=J)
    assert r.json() == {"ok": True, "on": False, "favorites": []}
    # 스크립트 없이 보낸 폼은 그 폴더로 돌아간다
    r = kim.post("/hwp/favorite", data={"dept": "기획처", "folder": "2026 공문", "on": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "folder=2026" in r.headers["location"]


def test_recent_folders_record_opens_and_work_per_user(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    for n in ("가", "나"):
        kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": n})
    kim.get("/hwp?dept=기획처&folder=가")
    _up(kim, app, "기획처", "회의록.hwpx", _hwpx(), folder="나")                       # 올린 폴더도 최근에
    page = kim.get("/hwp?dept=기획처").text
    side = _side(page)
    assert "최근 폴더" in side and side.index(">나<") < side.index(">가<")             # 최근 것이 위
    here = _side(kim.get("/hwp?dept=기획처&folder=나").text)
    assert ">나<" not in here                                                         # 지금 폴더는 빼고
    assert "최근 폴더" not in lee.get("/hwp").text                                       # 비면 절을 숨긴다


def test_editor_version_hidden_for_staff_and_shown_as_plain_status_for_admin(tmp_path, monkeypatch):
    app, kim, _ = _two(tmp_path, monkeypatch)
    app.state.accounts["boss"] = app.state.new_account("boss", PW, "dev")
    page = kim.get("/hwp").text
    assert "13.82.0.208" not in page and "hb-status" not in _side(page)
    dev = TestClient(app)
    assert _login(dev, "boss", PW)
    dpage = dev.get("/hwp").text
    assert "한글 편집기 연결됨" in dpage and "13.82.0.208" not in dpage


def test_drive_status_is_200_and_editor_down_keeps_buttons_disabled(tmp_path, monkeypatch):
    """구글 계정이 없는 것은 오류가 아니라 상태(200) — 화면은 이것을 먼저 물어 드라이브 목록을 부르지 않는다(콘솔 409 없음).
    한글 편집기가 꺼져도 「새 문서」·「양식으로 시작」 을 숨기지 않고 까닭과 함께 막아 둔다."""
    app, kim, _ = _two(tmp_path, monkeypatch)
    from zzaimy.ingest import gdrive_files
    monkeypatch.setattr(gdrive_files, "account_for", lambda *a, **k: "")
    r = kim.get("/api/hwp/drive/status")
    assert r.status_code == 200 and r.json() == {"status": "no_account", "account": ""}
    monkeypatch.setattr(gdrive_files, "account_for", lambda *a, **k: "kim@ync.ac.kr")
    assert kim.get("/api/hwp/drive/status").json()["status"] == "ok"
    js = (Path(webhwp.__file__).parent / "static" / "hwp-box.js").read_text(encoding="utf-8")
    assert js.index("/api/hwp/drive/status") < js.index("getJSON('/api/hwp/drive/browse')")     # 상태 먼저, 이었을 때만 목록
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": False, "version": "", "error": "x"})
    down = kim.get("/hwp?dept=기획처").text
    bar = down[down.index('class="hb-tools"'):down.index("</div>", down.index('class="hb-tools"'))]
    assert "한글 편집기 연결 안 됨" in bar and "/hwp/new?dept=" not in bar
    assert bar.count(" disabled ") == 2 and ">새 문서</button>" in bar and "양식으로 시작" in bar
    assert 'data-side-open' in down and 'id="hbSide"' in down                       # 좁은 화면은 「폴더」 단추로 여는 위치 판


def test_delete_in_place_returns_json(tmp_path, monkeypatch):
    app, kim, _ = _two(tmp_path, monkeypatch)
    fid = _up(kim, app, "기획처", "지울것.hwpx", _hwpx())
    r = kim.post(f"/hwp/{fid}/delete", headers=J)
    assert r.status_code == 200 and "휴지통" in r.json()["message"] and webhwp.get(app.state.db, fid) is None


def test_copy_goes_to_chosen_place_never_silently_to_common(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    _staff(app, "nod", "")                                                            # 소속 부서 없는 교직원
    nod = TestClient(app)
    assert _login(nod, "nod", PW)
    src = _up(kim, app, "공통", "보고서 공통 양식.hwpx", _hwpx())
    kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "작업함"})
    kim.get("/hwp?dept=기획처&folder=작업함")
    page = kim.get("/hwp?dept=기획처").text
    assert 'data-copy-dept="기획처" data-copy-folder="작업함"' in page                   # 창이 처음 여는 곳 = 내 부서의 마지막 폴더
    # 곳·이름을 주면 그대로(이름은 경로 문자를 걸러 확장자를 지킨다)
    j = kim.post(f"/hwp/{src}/copy", data={"dept": "기획처", "folder": "작업함", "name": "../우리 보고서"}, headers=J).json()
    p = Path(webhwp.get(app.state.db, j["id"])["path"])
    assert p.parent.name == "작업함" and p.parent.parent.name == "기획처" and p.suffix == ".hwpx" and "/" not in p.name
    # 부서를 안 주면 내 부서 맨 위 — 공통이 아니다
    r = kim.post(f"/hwp/{src}/copy", data={}, follow_redirects=False)
    nid = int(r.headers["location"].split("=")[-1])
    assert webhwp.dept_of_path(tmp_path, webhwp.get(app.state.db, nid)["path"]) == "기획처"
    assert Path(webhwp.get(app.state.db, nid)["path"]).name == "보고서.hwpx"
    # 소속 부서가 없으면 「내 폴더」 가 기본 — 공통으로 몰래 넣지 않는다
    r = nod.post(f"/hwp/{src}/copy", data={}, follow_redirects=False)
    nid = int(r.headers["location"].split("=")[-1])
    assert webhwp.dept_of_path(tmp_path, webhwp.get(app.state.db, nid)["path"]) == "~nod"
    # 못 쓰는 부서는 거절
    assert lee.post(f"/hwp/{src}/copy", data={"dept": "기획처"}, headers=J).status_code == 403


def test_copy_into_project_needs_write_share(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    from zzaimy.app import project_access as pa
    db = app.state.db
    src = _up(kim, app, "공통", "계획 공통 양식.hwpx", _hwpx())
    mine = db.create_project("grant", "AI 사업", owner="kim")
    ref = db.create_project("grant", "남의 사업", owner="lee")
    pa.set_shares(db, ref, "shared", [{"to": "user:kim", "level": "view"}])           # 참조(읽기 전용) 공유
    names = [p["name"] for p in kim.get("/api/hwp/projects").json()["projects"]]
    assert "AI 사업" in names and "남의 사업" not in names
    j = kim.post(f"/hwp/{src}/copy", data={"project": str(mine), "name": "계획"}, headers=J).json()
    f = webhwp.get(db, j["id"])
    assert webhwp.dept_of_path(tmp_path, f["path"]) == "기획처" and webhwp.folder_of(tmp_path, f["path"]) == "작업/AI 사업"
    assert kim.post(f"/hwp/{src}/copy", data={"project": str(ref)}, headers=J).status_code == 403
    pa.set_shares(db, ref, "shared", [{"to": "user:kim", "level": "edit"}])           # 함께 작업이면 된다
    assert kim.post(f"/hwp/{src}/copy", data={"project": str(ref)}, headers=J).status_code == 200


def test_new_folder_and_rename_only_where_writable(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    assert kim.post("/hwp/folder", data={"dept": "공통", "folder": "", "name": "내 것"}, headers=J).status_code == 403
    assert "data-new-folder" not in kim.get("/hwp?dept=공통").text
    assert "data-new-folder" in kim.get("/hwp?dept=기획처").text
    r = kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "새 폴더"}, headers=J)
    assert r.status_code == 200 and r.json()["path"] == "새 폴더"
    assert kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "새 폴더"}, headers=J).status_code == 400   # 같은 이름
    for bad in ("a/b", "..", ".숨김", "x" * 61):
        assert kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": bad}, headers=J).status_code == 400, bad
    fid = _up(kim, app, "기획처", "안.hwpx", _hwpx(), folder="새 폴더")
    kim.post("/hwp/favorite", data={"dept": "기획처", "folder": "새 폴더", "on": "1"}, headers=J)
    r = kim.post("/hwp/folder-rename", data={"dept": "기획처", "folder": "새 폴더", "name": "회의"}, headers=J)
    assert r.status_code == 200 and r.json()["path"] == "회의"
    p = Path(webhwp.get(app.state.db, fid)["path"])
    assert p.exists() and p.parent.name == "회의" and kim.get(f"/hwp/{fid}/file").status_code == 200
    assert "folder=%ED%9A%8C%EC%9D%98" in _side(kim.get("/hwp?dept=기획처").text)        # 즐겨찾기도 따라온다
    assert lee.post("/hwp/folder-rename", data={"dept": "기획처", "folder": "회의", "name": "x"}, headers=J).status_code == 403
    # 안의 문서를 다른 사람이 편집 중이면 이름을 바꾸지 않는다
    from zzaimy.app import rag_spaces
    cfg = rag_spaces.config(app.state.data_dir)
    cfg["grants"] = {"lee": ["dept:기획처"]}
    rag_spaces.save_config(cfg, app.state.data_dir)
    assert lee.post(f"/hwp/{fid}/lock").json()["ok"]
    assert kim.post("/hwp/folder-rename", data={"dept": "기획처", "folder": "회의", "name": "y"}, headers=J).status_code == 409


def test_personal_folder_is_owner_and_admin_only(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    page = kim.get("/hwp").text
    assert "내 폴더" in page and "/hwp?dept=~kim" in page
    fid = _up(kim, app, "~kim", "비밀.hwpx", _hwpx())
    assert kim.post("/hwp/folder", data={"dept": "~kim", "folder": "", "name": "초안"}, headers=J).status_code == 200
    mine = kim.get("/hwp?dept=~kim").text
    assert "비밀.hwpx" in mine and "<b>내 폴더</b>" in mine
    assert lee.get(f"/hwp/{fid}/file").status_code == 404
    assert "비밀.hwpx" not in lee.get("/hwp?dept=~kim").text                           # 남의 것은 자기 부서로
    assert lee.post("/hwp/upload", data={"dept": "~kim"}, files={"file": ("x.hwpx", _hwpx(), "x")}).status_code == 403
    assert "~kim" not in str(lee.get("/api/hwp/browse").json()["depts"])
    app.state.accounts["boss"] = app.state.new_account("boss", PW, "dev")
    dev = TestClient(app)
    assert _login(dev, "boss", PW)
    assert dev.get(f"/hwp/{fid}/file").status_code == 200                              # 관리자는 열 수 있다
    assert "/hwp?dept=~kim" not in dev.get("/hwp").text                                  # 왼쪽 목록에는 안 띄운다


def test_drive_pane_lists_shared_and_my_department_drive(tmp_path, monkeypatch):
    app, kim, _ = _two(tmp_path, monkeypatch)
    from zzaimy.app import webhwp_drive
    from zzaimy.ingest import gdrive_files
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, u, d=None: "kim@ync.ac.kr")
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: {})
    seen = []

    class _R:
        status_code = 200
        def __init__(self, j): self._j = j
        def json(self): return self._j

    class _H:
        def get(self, url, headers=None, params=None):
            seen.append((params or {}).get("q", ""))
            return _R({"files": [{"id": "S1aaaaaaaaaa", "name": "받은 계획.hwpx"}], "drives": []})

    monkeypatch.setattr(webhwp_drive, "_http", lambda: _H())
    db = app.state.db
    db.set_setting("google_root:기획처", "DEPTFOLDER01")
    db.set_setting("google_root_by:기획처", "dept@ync.ac.kr")
    db.set_setting("google_root:산학협력단", "OTHERFOLDER1")
    db.set_setting("google_root_by:산학협력단", "x@ync.ac.kr")
    j = kim.get("/api/hwp/drive/browse").json()
    assert j["dept_drives"] == [{"dept": "기획처", "name": "기획처 드라이브", "folder": "DEPTFOLDER01"}]   # 내 부서 것만
    j = kim.get("/api/hwp/drive/browse?shared=1").json()
    assert [f["name"] for f in j["files"]] == ["받은 계획.hwpx"] and "sharedWithMe" in seen[-1] and "drives" not in j
    js = (Path(__file__).parents[1] / "src/zzaimy/app/static/hwp-box.js").read_text(encoding="utf-8")
    assert "'가져오기'" in js and "공유 문서함" in js


def test_delete_many_checks_each_file(tmp_path, monkeypatch):
    app, kim, lee = _two(tmp_path, monkeypatch)
    a = _up(kim, app, "기획처", "가.hwpx", _hwpx())
    b = _up(kim, app, "기획처", "나.hwpx", _hwpx())
    other = _up(lee, app, "산학협력단", "남의 것.hwpx", _hwpx())
    from zzaimy.app import webhwp_templates
    webhwp_templates.ensure_all(app.state.db, tmp_path)
    tpl = next(f["id"] for f in app.state.db.list_files(kind="webhwp") if webhwp.locked(app.state.db, f["id"]))
    assert 'data-pick value="' in kim.get("/hwp?dept=기획처").text and "data-pick-all" in kim.get("/hwp?dept=기획처").text
    j = kim.post("/hwp/delete-many", json={"ids": [a, b, other, tpl, "x"]}).json()
    assert sorted(j["deleted"]) == sorted([a, b]) and len(j["rejected"]) == 3
    assert webhwp.get(app.state.db, other) and webhwp.get(app.state.db, tpl)          # 남의 부서·잠긴 양식은 그대로
    app.state.accounts["boss"] = app.state.new_account("boss", PW, "dev")
    dev = TestClient(app)
    assert _login(dev, "boss", PW)
    assert dev.post("/hwp/delete-many", json={"ids": [tpl]}).json()["deleted"] == [tpl]   # 관리자는 잠긴 양식도


def test_repath_files_treats_underscore_and_percent_literally(tmp_path):
    """폴더 이름의 _·% 가 LIKE 와일드카드로 다른 폴더의 파일 경로를 바꾸지 않는다."""
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    db.add_file("webhwp", "/x/a_b/1.hwpx", name="1")
    db.add_file("webhwp", "/x/aXb/2.hwpx", name="2")
    db.add_file("webhwp", "/x/5%/3.hwpx", name="3")
    db.add_file("webhwp", "/x/5zz/4.hwpx", name="4")
    db.repath_files("/x/a_b/", "/x/new/")
    db.repath_files("/x/5%/", "/x/pct/")
    paths = sorted(r["path"] for r in db.list_files(kind="webhwp"))
    assert paths == ["/x/5zz/4.hwpx", "/x/aXb/2.hwpx", "/x/new/1.hwpx", "/x/pct/3.hwpx"]


def test_list_wording_and_quiet_row_actions(tmp_path, monkeypatch):
    """목록 말·꼴 — 수정일·수정한 사람, 폴더 문서 수는 이름 옆, 줄 동작은 아이콘 단추(이름은 title·aria-label), 드라이브 아래 안내 줄 없음."""
    app, kim, _ = _two(tmp_path, monkeypatch)
    kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "결과보고서"})
    _up(kim, app, "기획처", "협조문.hwpx", _hwpx())
    page = kim.get("/hwp?dept=기획처").text
    assert "<span>수정일</span><span>수정한 사람</span>" in page and "고친 때" not in page and "고친 사람" not in page
    assert 'class="hb-count" title="문서 0개">0개</span>' in page and ">문서 0<" not in page
    assert "두 번 눌러" not in page and 'aria-label="「결과보고서」 이름 바꾸기"' in page
    assert 'aria-label="「협조문.hwpx」 내려받기"' in page and 'aria-label="「협조문.hwpx」 지우기"' in page
    assert ">내려받기</a>" not in page and ">지우기</button></form>" not in page
    assert "data-drive-note" not in page and "문서함으로 가져오기" not in page and 'class="hb-side-head">문서함<' in page


def test_remove_files_under_removes_detached_rows_only_in_that_folder(tmp_path):
    """문서를 먼저 지워 files.doc_id 가 비워져도 그 문서 폴더 아래 장부 줄은 지워진다 — 이름이 비슷한 옆 폴더는 그대로."""
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    db.add_file("intake", "/b/2026-국고-0072 계획_수정/원본.pdf", name="원본")
    db.add_file("intake", "/b/2026-국고-0072 계획_수정2/원본.pdf", name="원본")
    assert db.remove_files_under("/b/2026-국고-0072 계획_수정") == 1
    assert [r["path"] for r in db.list_files(kind="intake")] == ["/b/2026-국고-0072 계획_수정2/원본.pdf"]
