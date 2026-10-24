"""웹 한글 기안기 — 부서 한글 문서함(부서 디렉터리·권한), 저장 판 남기기, 편집기 고르기, 프록시."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import webhwp


def _hwpx(text: str = "본문") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # 고정 시각 — 바이트 비교 시험이 초가 바뀌는 순간(바쁜 기계)에 틀리지 않게
        z.writestr(zipfile.ZipInfo("mimetype", (2026, 1, 1, 0, 0, 0)), "application/hwp+zip")
        z.writestr(zipfile.ZipInfo("Contents/section0.xml", (2026, 1, 1, 0, 0, 0)), f"<p>{text}</p>")
    return buf.getvalue()


def _staff(app, uid: str, dept: str) -> None:
    app.state.accounts[uid] = app.state.new_account(uid, "pass-12345678", "staff")
    app.state.accounts[uid]["dept"] = dept


def _up(c, app, dept, name, data, folder=""):
    """올리고 그 문서 id — 올리기는 문서함으로 돌아간다."""
    r = c.post("/hwp/upload", data={"dept": dept, "folder": folder}, files={"file": (name, data, "x")}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"], r.headers.get("location")
    return max(f["id"] for f in app.state.db.list_files(kind="webhwp"))


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": True, "version": "13.82.0.208", "error": ""})
    app = _app(tmp_path)
    _staff(app, "kim", "기획처")
    _staff(app, "lee", "산학협력단")
    return app


def test_depts_for_roles():
    assert webhwp.depts_for("staff", "기획처", ("산학협력단",)) == ["기획처", "산학협력단", "공통"]
    assert webhwp.depts_for("staff", "", ()) == ["공통"]
    assert webhwp.depts_for("dev", "", (), ("기획처", "입학처")) == ["기획처", "입학처", "공통"]
    assert webhwp.depts_for("student", "기획처") == []


def test_upload_lands_in_department_directory_and_other_department_cannot_open(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    fid = _up(kim, app, "기획처", "사업계획서.hwpx", _hwpx())
    f = webhwp.get(app.state.db, fid)
    assert Path(f["path"]).parent.name == "기획처" and Path(f["path"]).parent.parent.name == "기안기"
    assert kim.get(f"/hwp/{fid}/file").content == _hwpx()
    assert "사업계획서.hwpx" in kim.get("/hwp").text
    # 다른 부서 사람은 목록에도 없고 열지도 못한다
    assert "사업계획서.hwpx" not in lee.get("/hwp").text
    assert lee.get(f"/hwp/{fid}/file").status_code == 404
    assert lee.post(f"/hwp/{fid}/save", content=_hwpx("남의 것")).status_code == 404
    # 남의 부서 디렉터리에 올리지도 못한다
    assert lee.post("/hwp/upload", data={"dept": "기획처"}, files={"file": ("x.hwpx", _hwpx(), "application/octet-stream")},
                    follow_redirects=False).status_code == 403


def test_extra_department_and_common_are_shared(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    from zzaimy.app import rag_spaces
    cfg = rag_spaces.config(app.state.data_dir)
    cfg["grants"] = {"lee": ["dept:기획처"]}
    rag_spaces.save_config(cfg, app.state.data_dir)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    a = _up(kim, app, "기획처", "공문.hwpx", _hwpx())
    b = _up(kim, app, "공통", "안내.hwpx", _hwpx())
    assert lee.get(f"/hwp/{a}/file").status_code == 200          # 추가 부서로 받은 기획처
    assert lee.get(f"/hwp/{b}/file").status_code == 200          # 공통


def test_save_keeps_previous_version(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    fid = _up(kim, app, "기획처", "보고.hwpx", _hwpx("처음"))
    r = kim.post(f"/hwp/{fid}/save", content=_hwpx("고침"))
    assert r.status_code == 200 and r.json()["ok"]
    p = Path(webhwp.get(app.state.db, fid)["path"])
    assert p.read_bytes() == _hwpx("고침")
    olds = list((p.parent / ".판").glob("보고 *.hwpx"))
    assert len(olds) == 1 and olds[0].read_bytes() == _hwpx("처음")
    assert webhwp.meta(app.state.db, fid)["saved_by"] == "kim"
    assert kim.post(f"/hwp/{fid}/save", content=b"not a hwp").status_code == 400


def test_old_hwp_becomes_hwpx_on_save(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    ole = b"\xd0\xcf\x11\xe0" + b"\0" * 60
    fid = _up(kim, app, "기획처", "옛문서.hwp", ole)
    assert kim.post(f"/hwp/{fid}/save", content=_hwpx()).status_code == 200
    f = webhwp.get(app.state.db, fid)
    assert f["path"].endswith("옛문서.hwpx") and f["name"] == "옛문서.hwpx"


def test_rejects_non_hangul_upload(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    r = kim.post("/hwp/upload", data={"dept": "기획처"}, files={"file": ("a.pdf", b"%PDF", "x")}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


def test_editor_choice_saved_per_user_and_shown_in_settings(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    assert webhwp.editor_of(app.state.db, "kim") == "gdocs"
    r = kim.post("/hwp/editor", data={"editor": "webhwp", "back": "/settings#myEditor"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings")
    assert webhwp.editor_of(app.state.db, "kim") == "webhwp"
    assert webhwp.editor_of(app.state.db, "lee") == "gdocs"
    page = kim.get("/settings").text
    assert "문서 편집기" in page and "웹 한글 기안기" in page
    # 밖으로 나가는 되돌아갈 주소는 받지 않는다
    r = kim.post("/hwp/editor", data={"editor": "gdocs", "back": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"].startswith("/hwp?")


def test_proxy_forwards_without_platform_session_cookie(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    seen = {}

    class _Resp:
        status_code = 200
        content = b"13.82.0.208"
        headers = __import__("httpx").Headers({"content-type": "text/plain", "set-cookie": "JSESSIONID=abc; Path=/webhwpctrl"})

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, params=None, headers=None, content=None):
            seen.update(method=method, url=url, headers=headers or {})
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    r = kim.get("/webhwpctrl/version.txt")
    assert r.status_code == 200 and r.text == "13.82.0.208"
    assert seen["url"].endswith("/webhwpctrl/version.txt")
    assert "zz_session" not in seen["headers"].get("cookie", "")
    assert "JSESSIONID" in r.headers.get("set-cookie", "")


def test_editor_page_lives_under_webhwpctrl_and_new_document_saves(tmp_path, monkeypatch):
    """기안기 스크립트는 페이지 폴더 기준으로 편집 틀을 연다 — 편집 화면은 /webhwpctrl/ 바로 아래. 새 문서는 첫 저장에 생긴다."""
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    r = kim.get("/hwp/new?dept=" + "기획처", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/webhwpctrl/aura-edit?dept=")
    assert "새 한글 문서" in kim.get(r.headers["location"]).text
    assert kim.get("/hwp/new?dept=" + "산학협력단", follow_redirects=False).status_code == 403
    j = kim.post("/hwp/new?dept=기획처&title=회의록", content=_hwpx("회의")).json()
    assert j["ok"] and j["url"] == f"/webhwpctrl/aura-edit?id={j['id']}"
    assert Path(webhwp.get(app.state.db, j["id"])["path"]).name == "회의록.hwpx"
    r = kim.get(f"/hwp/{j['id']}/edit", follow_redirects=False)
    assert r.headers["location"] == f"/webhwpctrl/aura-edit?id={j['id']}"
    assert "회의록.hwpx" in kim.get(r.headers["location"]).text


def test_proxy_refuses_paths_outside_editor_app(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    called = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            called.append(a)
            raise AssertionError("나가면 안 된다")

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    for p in ("/webhwpctrl/..%2Fwebhwpctrl-admin%2F", "/webhwpctrl/js/..%2F..%2Fmanager", "/webhwpctrl/%2E%2E/filterserver",
              "/webhwpctrl/a%5C..%5Cb"):
        assert kim.get(p).status_code == 400, p
    assert called == []


def test_upload_and_save_bodies_are_capped(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    from zzaimy.app import webhwp_routes
    monkeypatch.setattr(webhwp_routes, "MAX_BYTES", 1000)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    big = b"PK" + b"0" * 5000
    assert kim.post("/hwp/new?dept=기획처", content=big).status_code == 413


def test_department_folders_browse_and_trash(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    r = kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "2026 공문"}, follow_redirects=False)
    assert r.status_code == 303 and "folder=" in r.headers["location"]
    kim.post("/hwp/folder", data={"dept": "기획처", "folder": "2026 공문", "name": "10월"})
    fid = _up(kim, app, "기획처", "협조문.hwpx", _hwpx(), folder="2026 공문/10월")
    p = Path(webhwp.get(app.state.db, fid)["path"])
    assert p.parent.name == "10월" and p.parent.parent.name == "2026 공문" and p.parent.parent.parent.name == "기획처"
    top = kim.get("/hwp?dept=기획처").text
    assert "folder=2026" in top and "협조문.hwpx" not in top            # 폴더 안 문서는 그 폴더에서만
    inside = kim.get("/hwp?dept=기획처&folder=2026 공문/10월").text
    assert "협조문.hwpx" in inside and "10월" in inside
    # 폴더로 부서 밖·숨김 폴더에 나가지 못한다
    for bad in ("../산학협력단", ".판", "a/../../x"):
        assert kim.get("/hwp?dept=기획처&folder=" + bad).status_code == 400, bad
        assert kim.post("/hwp/folder", data={"dept": "기획처", "folder": bad, "name": "x"}).status_code == 400, bad
    seen = lee.get("/hwp?dept=기획처")                    # 못 쓰는 부서를 고르면 자기 부서를 보여 준다
    assert seen.status_code == 200 and "folder=2026" not in seen.text and "<b>산학협력단</b>" in seen.text
    assert lee.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "남의 것"}).status_code == 403
    # 지우기 — 부서 휴지통으로, 목록·장부에서 빠진다
    assert lee.post(f"/hwp/{fid}/delete").status_code == 404
    r = kim.post(f"/hwp/{fid}/delete", follow_redirects=False)
    assert r.status_code == 303 and webhwp.get(app.state.db, fid) is None and not p.exists()
    assert list((p.parents[2] / ".휴지통").glob("협조문 *.hwpx"))
    assert "협조문.hwpx" not in kim.get("/hwp?dept=기획처&folder=2026 공문/10월").text


def test_rag_spaces_config_returns_a_copy(tmp_path):
    """설정 파일이 없을 때 받은 기본값을 고쳐도 다음 호출에 번지지 않는다(시험 사이 권한이 새던 버그)."""
    from zzaimy.app import rag_spaces
    a = rag_spaces.config(tmp_path)
    a["grants"] = {"x": ["dept:기획처"]}
    a.setdefault("dept_of_area", {})["가"] = "나"
    b = rag_spaces.config(tmp_path)
    assert b.get("grants", {}).get("x") is None and "가" not in b.get("dept_of_area", {})


def test_common_templates_built_once_and_fillable(tmp_path, monkeypatch):
    """구글 공통 양식 정의 → 한글 공통 양식(공통/공통 양식). 다시 불러도 새로 만들지 않고, 작업본 절이 그대로 채워진다."""
    from zzaimy.app import webhwp_templates as wt
    from zzaimy.ingest import hwpx_fill
    from zzaimy.ingest.gdocs_templates import SPECS
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    first = wt.ensure_all(db, base)
    assert len(first) == len(SPECS) + len(wt.OFFICIAL) + len(wt.GRANT) and all(x["made"] for x in first)
    assert all(Path(webhwp.get(db, x["id"])["path"]).parent.name in ("공통 양식", "공문", "국고 사업") for x in first)
    assert not any(x["made"] for x in wt.ensure_all(db, base))
    src = wt.path_for(db, "minutes")
    heads = [b["text"] for b in SPECS["minutes"]["blocks"] if "h" in b]
    rep = hwpx_fill.fill(src, [{"heading": heads[0], "items": [("text", "시험 회의 개요")]}], tmp_path / "out.hwpx", remove_boxes=True)
    assert rep["filled"] == [heads[0]] and not rep["checks"] and rep["boxes_removed"] > 0
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    assert "회의록" in kim.get("/hwp?dept=공통&folder=공통 양식").text


def test_common_templates_are_locked_and_copyable(tmp_path, monkeypatch):
    """공통 양식은 모두의 한글 완성본이 원본으로 쓴다 — 교직원은 고치거나 지우지 못하고 복사해 쓴다. 덮어쓴 파일은 정의대로 다시 만든다."""
    from zzaimy.app import webhwp_templates as wt
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    tid = {x["spec"]: x["id"] for x in wt.ensure_all(db, base)}["plan"]
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    assert kim.post(f"/hwp/{tid}/save", content=_hwpx("바꿈")).status_code == 403
    assert kim.post(f"/hwp/{tid}/delete").status_code == 403
    r = kim.post(f"/hwp/{tid}/copy", data={}, follow_redirects=False)
    assert r.status_code == 303 and "aura-edit?id=" in r.headers["location"]
    new = int(r.headers["location"].split("=")[-1])
    p = Path(webhwp.get(db, new)["path"])
    assert p.parent.name == "기획처" and p.name == "사업계획서.hwpx"
    assert kim.post(f"/hwp/{new}/save", content=_hwpx("내 것")).status_code == 200
    # 디스크에서 양식이 바뀌면 원본 서식으로 쓰지 않고, 다시 맞추면 정의대로 돌아온다
    tp = Path(webhwp.get(db, tid)["path"])
    tp.write_bytes(_hwpx("몰래 바꿈"))
    assert wt.path_for(db, "plan") is None
    wt.ensure_all(db, base)
    assert wt.path_for(db, "plan") == tp and tp.read_bytes() != _hwpx("몰래 바꿈")


def test_concurrent_editing_shares_fields_and_merges_on_save(tmp_path, monkeypatch):
    """같은 문서를 둘이 동시에 — 칸에 쓴 글은 서로 받지만, 저장은 편집 잠금을 쥔 사람만(서버가 409 로 막는다). 잠금 쥔 사람이 저장할 때 상대 칸 글을 합친다."""
    app = _setup(tmp_path, monkeypatch)
    from zzaimy.app import rag_spaces, webhwp_templates as wt, webhwp_write as ww
    cfg = rag_spaces.config(app.state.data_dir)
    cfg["grants"] = {"lee": ["dept:기획처"]}
    rag_spaces.save_config(cfg, app.state.data_dir)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    form = wt.build(wt.GRANT["budget_change"])
    fid = _up(kim, app, "기획처", "공동.hwpx", form)
    assert kim.post(f"/hwp/{fid}/lock").json()["ok"]
    j = lee.post(f"/hwp/{fid}/lock").json()
    assert j["ok"] is False and j["by"] == "kim" and not j.get("readonly")
    assert "kim 편집 중" in lee.get("/hwp?dept=기획처").text
    # kim 은 사업명, lee 는 담당자 칸을 쓴다 — 서로 받는다
    kim.post(f"/hwp/{fid}/live", json={"field": "칸:사업명", "text": "AI 사업", "since": 0})
    r = lee.post(f"/hwp/{fid}/live", json={"field": "칸:담당자", "text": "이몽룡", "since": 0}).json()
    assert [c["field"] for c in r["changes"]] == ["칸:사업명"] and r["changes"][0]["text"] == "AI 사업"
    r2 = kim.post(f"/hwp/{fid}/live", json={"since": 1}).json()
    assert [c["field"] for c in r2["changes"]] == ["칸:담당자"]
    # 잠금을 쥔 kim 이 있는 동안 lee 의 저장·이름 바꾸기·지우기는 서버가 막는다
    lee_copy, _ = ww.set_fields(form, {"칸:담당자": "이몽룡"})
    before = Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()
    r = lee.post(f"/hwp/{fid}/save", content=lee_copy)
    assert r.status_code == 409 and "편집 중이라 저장하지 않았습니다" in r.json()["detail"]
    assert Path(webhwp.get(app.state.db, fid)["path"]).read_bytes() == before
    assert lee.post(f"/hwp/{fid}/rename", json={"name": "남의 이름"}).status_code == 409
    assert lee.post(f"/hwp/{fid}/delete", headers={"Accept": "application/json"}).status_code == 409
    assert webhwp.get(app.state.db, fid) is not None
    # kim 이 자기 판(담당자는 비어 있음)을 저장하면 lee 가 칸에 쓴 담당자가 합쳐진다
    kim_copy, _ = ww.set_fields(form, {"칸:사업명": "AI 사업"})
    out = kim.post(f"/hwp/{fid}/save", content=kim_copy).json()
    assert out["ok"] and out["merged"] == ["칸:담당자"]
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(app.state.db, fid)["path"]).read_bytes())}
    assert vals["칸:사업명"] == "AI 사업" and vals["칸:담당자"] == "이몽룡"
    kim.post(f"/hwp/{fid}/unlock")
    assert lee.post(f"/hwp/{fid}/lock").json()["ok"]
    assert lee.post(f"/hwp/{fid}/save", content=lee_copy).status_code == 200      # 잠금을 쥐면 저장된다
    with app.state.db._conn() as conn:                      # 창이 끊겨 늘리지 못하면 임대 시간이 지나 저절로 풀린다
        conn.execute("UPDATE webhwp_field_locks SET lease_until = 0 WHERE fid = ?", (fid,))
    assert kim.post(f"/hwp/{fid}/lock").json()["ok"]


def test_drive_import_and_upload_final(tmp_path, monkeypatch):
    """드라이브 → 부서 디렉터리로 받아 기안기 편집 → 최종본을 드라이브에(앞서 올린 판은 휴지통). 계정이 없으면 409."""
    app = _setup(tmp_path, monkeypatch)
    from zzaimy.app import webhwp_drive
    from zzaimy.ingest import gdrive_files
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, u, d=None: "")
    assert kim.get("/api/hwp/drive").status_code == 409
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, u, d=None: "kim@ync.ac.kr")
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: {})
    calls = []

    class _R:
        def __init__(self, code=200, j=None, content=b""):
            self.status_code, self._j, self.content = code, j or {}, content
        def json(self):
            return self._j

    class _H:
        def get(self, url, headers=None, params=None):
            if params and params.get("alt") == "media":
                return _R(content=_hwpx("드라이브"))
            if url.endswith("/files"):
                return _R(j={"files": [{"id": "F1aaaaaaaaaa", "name": "계획서.hwpx", "modifiedTime": "2026-10-21T00:00:00Z"},
                                       {"id": "F2", "name": "그림.png"}]})
            return _R(j={"id": "F1aaaaaaaaaa", "name": "계획서.hwpx", "parents": ["P1"], "size": "100"})
        def patch(self, url, **k):
            calls.append(("trash", url))
            return _R()

    monkeypatch.setattr(webhwp_drive, "_http", lambda: _H())
    j = kim.get("/api/hwp/drive").json()
    assert [f["name"] for f in j["files"]] == ["계획서.hwpx"]                     # 한글 파일만
    r = kim.post("/hwp/drive/import", data={"file_id": "F1aaaaaaaaaa", "dept": "기획처", "folder": ""}, follow_redirects=False)
    assert r.status_code == 303 and "aura-edit?id=" in r.headers["location"]
    fid = int(r.headers["location"].split("=")[-1])
    assert Path(webhwp.get(app.state.db, fid)["path"]).parent.name == "기획처"
    assert webhwp.meta(app.state.db, fid)["drive"]["parent"] == "P1"
    assert kim.post("/hwp/drive/import", data={"file_id": "../x?alt=media", "dept": "기획처"}).status_code == 400
    ups = []

    def _up(email, data, name, mime, folder, http=None, reuse=True):
        ups.append(folder)
        if folder == "P1":
            raise PermissionError("남의 폴더")
        return {"id": f"U{len(ups)}", "url": f"https://drive/U{len(ups)}", "name": name}

    monkeypatch.setattr(gdrive_files, "upload_file", _up)
    monkeypatch.setattr(gdrive_files, "ensure_folder", lambda e, parts, http=None: "AURA")
    a = kim.post(f"/hwp/{fid}/to-drive").json()
    assert a["ok"] and ups == ["P1", "AURA"]                                    # 원본 폴더 먼저, 안 되면 우리 폴더
    b = kim.post(f"/hwp/{fid}/to-drive").json()
    assert b["url"] != a["url"] and any(c[0] == "trash" for c in calls)         # 앞서 올린 판은 휴지통


def test_start_from_template_and_project_form(tmp_path, monkeypatch):
    """양식으로 시작(잠긴 양식 목록 → 내 부서 폴더에 복사) · 문서함의 한글 양식을 기안기 작업본으로(필드 박기)."""
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    groups = kim.get("/api/hwp/templates").json()["groups"]
    names = [x["name"] for g in groups for x in g["items"]]
    assert any("기안문" in n for n in names) and any("사업계획서" in n for n in names)
    assert groups[0]["group"].startswith("공통 양식/공문")                       # 공문이 먼저
    tid = next(x["id"] for g in groups for x in g["items"] if "기안문" in x["name"])
    r = kim.post(f"/hwp/{tid}/copy", data={"dept": "기획처", "folder": ""}, follow_redirects=False)
    new = int(r.headers["location"].split("=")[-1])
    assert Path(webhwp.get(app.state.db, new)["path"]).parent.name == "기획처"
    # 문서함의 한글 양식 → 작업본(hwpx 는 변환 없이, 쓸 자리 필드를 박는다)
    from zzaimy.app import webhwp_templates as wt
    form = wt.build({"id": "x", "title": "x", "cover": ["시험 양식"], "blocks": [
        {"table": {"columns": ["항목", "내용"], "rows": [["기관명", ""]], "widths": [1, 3]}}, {"h": 1, "text": "1. 추진 계획"}]})
    import zipfile, io, re as _re
    plain = io.BytesIO()                                                          # 필드 없는 원본처럼 — 필드를 지운 사본
    with zipfile.ZipFile(io.BytesIO(form)) as zin, zipfile.ZipFile(plain, "w") as zout:
        for info in zin.infolist():
            b = zin.read(info.filename)
            if info.filename.startswith("Contents/section"):
                b = _re.sub(r"<hp:ctrl><hp:fieldBegin.*?</hp:fieldBegin></hp:ctrl>|<hp:ctrl><hp:fieldEnd[^>]*/></hp:ctrl>", "", b.decode()).encode()
            zout.writestr(info, b)
    src = tmp_path / "사업 양식.hwpx"
    src.write_bytes(plain.getvalue())
    db = app.state.db
    did = db.add_document("사업 양식.hwpx", str(src), owner="kim", dept="기획처")
    db.update_document(did, status="reviewed")
    r = kim.post(f"/doc/{did}/webhwp", data={}, follow_redirects=False)
    assert r.status_code == 303 and "aura-edit?id=" in r.headers["location"], r.headers.get("location")
    wid = int(r.headers["location"].split("=")[-1])
    m = webhwp.meta(db, wid)
    assert m["source"] == f"doc:{did}" and "칸:기관명" in m["fields"] and "절:1. 추진 계획" in m["fields"]


def test_autosave_skips_unchanged_and_keeps_one_version_per_gap(tmp_path, monkeypatch):
    """자동 저장이 몇 초마다 돌아도 — 같은 내용은 쓰지 않고, 앞 판은 VERSION_GAP 안에 하나만 남는다."""
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    fid = webhwp.add(db, tmp_path, "기획처", "가.hwpx", _hwpx(), owner="kim")
    p = Path(webhwp.get(db, fid)["path"])
    vdir = p.parent / ".판"
    webhwp.save(db, tmp_path, fid, p.read_bytes(), user="kim")                     # 바뀐 것 없음
    assert not vdir.exists() or not list(vdir.iterdir())
    for i in range(3):
        webhwp.save(db, tmp_path, fid, _hwpx() + bytes([i]), user="kim")
    assert len(list(vdir.iterdir())) == 1 and p.read_bytes().endswith(bytes([2]))
    monkeypatch.setattr(webhwp, "VERSION_GAP", 0)
    webhwp.save(db, tmp_path, fid, _hwpx() + b"x", user="kim")
    assert len(list(vdir.iterdir())) == 2


def test_version_pruning_does_not_touch_files_sharing_a_name_prefix(tmp_path, monkeypatch):
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    a = webhwp.add(db, tmp_path, "기획처", "계획.hwpx", _hwpx(), owner="kim")
    b = webhwp.add(db, tmp_path, "기획처", "계획 2.hwpx", _hwpx(), owner="kim")
    monkeypatch.setattr(webhwp, "VERSION_GAP", 0)
    monkeypatch.setattr(webhwp, "VERSION_KEEP", 1)
    for i in range(3):
        webhwp.save(db, tmp_path, b, _hwpx() + bytes([i]), user="kim")
    vdir = Path(webhwp.get(db, b)["path"]).parent / ".판"
    before = sorted(x.name for x in vdir.iterdir() if x.name.startswith("계획 2 "))
    for i in range(3):
        webhwp.save(db, tmp_path, a, _hwpx() + bytes([i]), user="kim")
    assert sorted(x.name for x in vdir.iterdir() if x.name.startswith("계획 2 ")) == before


def test_document_box_page_finder_layout_without_editor_radio(tmp_path, monkeypatch):
    """문서함 화면 — 제목은 「문서함」, 왼쪽에 부서 폴더·구글 드라이브, 편집기 라디오 없음, 새 폴더는 목록 줄에서 바로."""
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    kim.post("/hwp/folder", data={"dept": "기획처", "folder": "", "name": "2026 공문"})
    _up(kim, app, "기획처", "협조문.hwpx", _hwpx(), folder="2026 공문")
    page = kim.get("/hwp?dept=기획처").text
    assert "<title>문서함" in page and "한글 문서함" not in page.split("<main", 1)[-1]
    assert "부서 폴더" in page and "구글 드라이브" in page and "내 드라이브" in page and "data-drive-hint" in page
    assert "/settings" in page and "학교 구글 계정을 연결하면 드라이브도 여기서 봅니다" in page
    assert "한글 완성본 열 곳" not in page and 'name="editor"' not in page and "/hwp/editor" not in page
    assert 'class="hb-btn primary" href="/hwp/new?dept=' in page                    # 주 단추는 새 문서 하나
    assert page.count("hb-btn primary") == 1                                        # 새 폴더는 창 없이 목록 줄에서 바로
    assert "data-new-folder" in page and 'id="hwFolder"' not in page and "solar:add-folder-linear" in page
    assert "data-drive-open" not in page                                            # 드라이브는 왼쪽 「구글 드라이브」 에서
    assert "복사할 폴더와 이름을 정한 뒤 기안기로 엽니다" in page and " 에 복사해" not in page
    assert "/static/hwp-box.js" in page and "/static/hwp-box.css" in page
    inside = kim.get("/hwp?dept=기획처&folder=2026 공문").text
    assert "협조문.hwpx" in inside and 'href="/hwp?dept=%EA%B8%B0%ED%9A%8D%EC%B2%98" aria-label="위 폴더로"' in inside
    assert "13.82.0.208" not in page and "기안기 연결됨" not in page              # 기안기 판·주소는 교직원 화면에 보이지 않는다
    # 기안기 서버가 없으면 새 문서·양식 단추를 숨기지 않고 까닭과 함께 막아 둔다(내려받기·올리기는 그대로)
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": False, "version": "", "error": "x"})
    down = kim.get("/hwp?dept=기획처&folder=2026 공문").text
    assert "/hwp/new?dept=" not in down and "data-tpl-open" not in down and "한글 편집기 연결 안 됨" in down


def test_drive_browse_without_google_account_is_409(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    from zzaimy.ingest import gdrive_files
    monkeypatch.setattr(gdrive_files, "account_for", lambda *a, **k: "")
    assert kim.get("/api/hwp/drive/browse").status_code == 409
