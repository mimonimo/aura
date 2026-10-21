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
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", f"<p>{text}</p>")
    return buf.getvalue()


def _staff(app, uid: str, dept: str) -> None:
    app.state.accounts[uid] = app.state.new_account(uid, "pass-12345678", "staff")
    app.state.accounts[uid]["dept"] = dept


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
    r = kim.post("/hwp/upload", data={"dept": "기획처"}, files={"file": ("사업계획서.hwpx", _hwpx(), "application/octet-stream")},
                 follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/edit")
    fid = int(r.headers["location"].split("/")[2])
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
    a = int(kim.post("/hwp/upload", data={"dept": "기획처"}, files={"file": ("공문.hwpx", _hwpx(), "x")},
                     follow_redirects=False).headers["location"].split("/")[2])
    b = int(kim.post("/hwp/upload", data={"dept": "공통"}, files={"file": ("안내.hwpx", _hwpx(), "x")},
                     follow_redirects=False).headers["location"].split("/")[2])
    assert lee.get(f"/hwp/{a}/file").status_code == 200          # 추가 부서로 받은 기획처
    assert lee.get(f"/hwp/{b}/file").status_code == 200          # 공통


def test_save_keeps_previous_version(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    fid = int(kim.post("/hwp/upload", data={"dept": "기획처"}, files={"file": ("보고.hwpx", _hwpx("처음"), "x")},
                       follow_redirects=False).headers["location"].split("/")[2])
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
    fid = int(kim.post("/hwp/upload", data={"dept": "기획처"}, files={"file": ("옛문서.hwp", ole, "x")},
                       follow_redirects=False).headers["location"].split("/")[2])
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
