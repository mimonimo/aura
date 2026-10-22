"""기안기 내려받기 형식(필터 서버 변환)·다른 이름으로 저장(부서 폴더·드라이브)·이름 바꾸기."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _hwpx, _setup, _up
from zzaimy.app import webhwp, webhwp_export


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


class _R:
    def __init__(self, status=200, json=None, content=b""):
        self.status_code, self._j, self.content = status, json, content

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("x", request=None, response=None)

    def json(self):
        return self._j


class _Filter:
    """기안기 필터 서버 흉내 — open → save(format) → get. 실제 서버처럼 DOCX 는 거절, OOXML 은 docx."""
    OUT = {"HWP": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1hwp", "PDF": b"%PDF-1.5 x", "HWPX": b"PK\x03\x04", "OOXML": None}

    def __init__(self, *a, **k):
        self.calls = []

    def post(self, url, files=None, data=None, headers=None, timeout=None):
        self.calls.append((url, dict(data or {})))
        if url.endswith("/webhwpctrl/open"):
            return _R(json={"id": "doc1", "json": "{}"})
        fmt = data["format"]
        if fmt not in self.OUT:
            return _R(json={"result": False, "resultCode": -2147401697})
        return _R(json={"result": True, "fileName": f"out.{fmt.lower()}"})

    def get(self, url, timeout=None):
        fmt = url.rsplit(".", 1)[1].upper()
        return _R(content=self.OUT[fmt] if self.OUT[fmt] is not None else _docx())

    def close(self):
        pass


def test_formats_match_editor_menu():
    page = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/templates/hwp_edit.html").read_text(encoding="utf-8")
    keys = re.findall(r"\('(\w+)', '[^']+'\)", page[page.index("내려받기</div>"):])
    assert keys == list(webhwp_export.FORMATS)


def test_export_converts_through_filter_and_checks_access(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    import httpx
    flt = _Filter()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: flt)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    fid = _up(kim, app, "기획처", "계획서.hwpx", _hwpx())
    r = kim.get(f"/hwp/{fid}/export?fmt=hwpx")                      # 같은 형식은 바꾸지 않고 그대로
    assert r.status_code == 200 and r.content == _hwpx() and not flt.calls
    r = kim.get(f"/hwp/{fid}/export?fmt=pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"
    assert "%EA%B3%84%ED%9A%8D%EC%84%9C.pdf" in r.headers["content-disposition"]
    r = kim.get(f"/hwp/{fid}/export?fmt=hwp")
    assert r.status_code == 200 and r.content[:4] == b"\xd0\xcf\x11\xe0"
    r = kim.get(f"/hwp/{fid}/export?fmt=docx")
    assert r.status_code == 200 and r.content[:2] == b"PK" and "계획서.docx" in r.headers["content-disposition"].replace(
        "%EA%B3%84%ED%9A%8D%EC%84%9C", "계획서")
    assert [d["format"] for u, d in flt.calls if u.endswith("/save")] == ["PDF", "HWP", "OOXML"]
    assert kim.get(f"/hwp/{fid}/export?fmt=exe").status_code == 400
    assert lee.get(f"/hwp/{fid}/export?fmt=pdf").status_code == 404     # 다른 부서는 못 받는다
    # 필터가 엉뚱한 것을 돌려주면 파일로 내보내지 않는다
    monkeypatch.setitem(_Filter.OUT, "PDF", b"<html>error</html>")
    assert kim.get(f"/hwp/{fid}/export?fmt=pdf").status_code == 502


def test_save_as_into_department_folder(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    fid = _up(kim, app, "기획처", "계획서.hwpx", _hwpx())
    webhwp.make_folder(Path(app.state.data_dir), "기획처", "", "작업")
    j = kim.post(f"/hwp/save-as?dept=기획처&folder=작업&name=계획서 2차.hwpx&src={fid}", content=_hwpx("새 판")).json()
    assert j["ok"] and j["url"] == f"/webhwpctrl/aura-edit?id={j['id']}" and j["name"] == "계획서 2차.hwpx"
    f = webhwp.get(app.state.db, j["id"])
    assert Path(f["path"]).read_bytes() == _hwpx("새 판") and webhwp.folder_of(Path(app.state.data_dir), f["path"]) == "작업"
    assert webhwp.meta(app.state.db, j["id"])["source"] == f"다른 이름:{fid}"
    # 같은 이름이면 따로 둔다, 새 문서(src 없음)도 같은 길로 만든다
    j2 = kim.post("/hwp/save-as?dept=기획처&folder=작업&name=계획서 2차", content=_hwpx()).json()
    assert j2["name"] == "계획서 2차 (2).hwpx" and webhwp.meta(app.state.db, j2["id"])["source"] == "new"
    assert kim.post("/hwp/save-as?dept=기획처&name=", content=_hwpx()).status_code == 400
    assert kim.post("/hwp/save-as?dept=기획처&name=x", content=b"not hwpx").status_code == 400
    assert lee.post("/hwp/save-as?dept=기획처&name=x", content=_hwpx()).status_code == 403   # 남의 부서 폴더
    assert lee.post(f"/hwp/save-as?dept=산학협력단&name=x&src={fid}", content=_hwpx()).status_code == 404  # 못 보는 문서의 사본


def test_save_as_to_drive(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    from zzaimy.ingest import gdrive_files
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, u, d=None: "")
    assert kim.post("/hwp/save-as/drive?name=x", content=_hwpx()).status_code == 409
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, u, d=None: "kim@ync.ac.kr")
    seen = []

    def _upload(email, data, name, mime, parent=None, http=None, reuse=True, **k):
        seen.append((email, name, mime, parent, reuse))
        return {"id": "DRV1234567890", "name": name, "url": "https://drive/x", "mime": mime}
    monkeypatch.setattr(gdrive_files, "upload_file", _upload)
    j = kim.post("/hwp/save-as/drive?name=보고서&folder=FOLDER12345", content=_hwpx()).json()
    assert j["ok"] and j["drive_url"] == "https://drive/x" and "id" not in j
    assert seen[-1] == ("kim@ync.ac.kr", "보고서.hwpx", "application/hwp+zip", "FOLDER12345", False)
    assert kim.post("/hwp/save-as/drive?name=x&folder=../etc", content=_hwpx()).status_code == 400
    # 새 문서 만들기 — 드라이브에 올리고 부서 폴더에 작업본을 두어 자동 저장이 이어진다(최종본 올리기는 같은 드라이브 폴더로)
    j = kim.post("/hwp/save-as/drive?name=보고서&keep=1&dept=기획처", content=_hwpx()).json()
    assert seen[-1][3] is None and j["url"] == f"/webhwpctrl/aura-edit?id={j['id']}"
    m = webhwp.meta(app.state.db, j["id"])
    assert m["drive"]["parent"] == "" and m["drive_out"]["id"] == "DRV1234567890" and m["dept"] == "기획처"
    assert kim.post("/hwp/save-as/drive?name=x&keep=1&dept=산학협력단", content=_hwpx()).status_code == 403


def test_rename_keeps_extension_versions_and_dedups(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(webhwp, "VERSION_GAP", 0)
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    a = _up(kim, app, "기획처", "새 문서.hwpx", _hwpx())
    _up(kim, app, "기획처", "회의록.hwpx", _hwpx("다른"))
    assert kim.post(f"/hwp/{a}/save", content=_hwpx("둘째")).status_code == 200      # 앞 판이 .판/ 에 남는다
    r = kim.post(f"/hwp/{a}/rename", json={"name": "회의록.hwp"})                   # 확장자는 그대로, 같은 이름은 (2)
    assert r.status_code == 200 and r.json()["name"] == "회의록 (2).hwpx"
    f = webhwp.get(app.state.db, a)
    p = Path(f["path"])
    assert f["name"] == "회의록 (2).hwpx" and p.exists() and p.read_bytes() == _hwpx("둘째")
    assert not (p.parent / "새 문서.hwpx").exists()
    assert [v.name.startswith("회의록 (2) ") for v in (p.parent / ".판").iterdir()] == [True]
    assert kim.get(f"/hwp/{a}/file").content == _hwpx("둘째")
    assert kim.post(f"/hwp/{a}/rename", json={"name": "  "}).status_code == 400
    assert lee.post(f"/hwp/{a}/rename", json={"name": "x"}).status_code == 404


def test_editor_page_offers_save_dialog_and_folder_path(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    new = kim.get("/webhwpctrl/aura-edit?dept=기획처").text
    assert "만들기" in new and "data-sa" in new and "에 생깁니다" not in new and "data-title" not in new
    webhwp.make_folder(Path(app.state.data_dir), "기획처", "", "작업")
    j = kim.post("/hwp/save-as?dept=기획처&folder=작업&name=계획", content=_hwpx()).json()
    page = kim.get(j["url"]).text
    assert "다른 이름으로 저장" in page and "지금 저장" not in page and "기획처 › 작업" in page
    assert "/hwp?dept=%EA%B8%B0%ED%9A%8D%EC%B2%98&amp;folder=%EC%9E%91%EC%97%85" in page or "folder=%EC%9E%91%EC%97%85" in page
    for k in webhwp_export.FORMATS:
        assert f"/hwp/{j['id']}/export?fmt={k}" in page
