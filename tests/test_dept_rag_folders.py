"""부서 RAG·라이브러리 폴더 — 기본은 공통 RAG, 부서 RAG 는 만들어 고른 문서만, 폴더의 검색 범위를 바꾸면 문서가 함께 옮겨 간다."""
import io

from tests.test_accounts import _app, _login
from fastapi.testclient import TestClient
from zzaimy.app import library_folders as lf
from zzaimy.app import rag_spaces


def _dev(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    return app, c


def test_dept_rag_create_move_and_delete(tmp_path):
    app, c = _dev(tmp_path)
    db = app.state.db
    r = c.post("/dev/rag/dept", data={"action": "create", "name": "교무처"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert "교무처" in rag_spaces.known_depts(rag_spaces.config(tmp_path), {})
    assert "공통" not in [x["dept"] for x in rag_spaces.dept_rag_rows(db, rag_spaces.config(tmp_path), {})]
    d = db.add_document("지침.hwp", "x", doc_type="grant")
    assert db.get_document(d)["dept"] == "공통" and db.get_document(d)["access_level"] == "public"   # 기본은 공통 RAG
    assert c.post("/docs/scope", json={"ids": [d], "dept": "교무처"}).json()["moved"] == 1
    assert (db.get_document(d)["dept"], db.get_document(d)["access_level"]) == ("교무처", "dept")
    assert c.post("/docs/scope", json={"ids": [d], "dept": "없는부서"}).status_code == 400
    c.post("/dev/rag/dept", data={"action": "delete", "name": "교무처"})
    assert (db.get_document(d)["dept"], db.get_document(d)["access_level"]) == ("공통", "public")      # 지우면 공통으로


def test_library_folders_upload_and_scope(tmp_path):
    app, c = _dev(tmp_path)
    db = app.state.db
    c.post("/dev/rag/dept", data={"action": "create", "name": "교무처"})
    r = c.post("/library/folders", data={"name": "학사 지침", "dept": "공통"}, follow_redirects=False)
    assert "folder=f%3A" in r.headers["location"]
    key = next(f["key"] for f in lf.folders(db, tmp_path) if f["name"] == "학사 지침")
    r = c.post("/library/upload", data={"folder": key, "doc_type": "auto"},
               files=[("files", ("안내.txt", io.BytesIO("학사 안내 본문".encode()), "text/plain"))], follow_redirects=False)
    assert "ok=" in r.headers["location"]
    f = next(f for f in lf.folders(db, tmp_path) if f["key"] == key)
    assert f["docs"] == 1 and f["dept"] == "공통"
    page = c.get("/?type=all&folder=" + key).text
    assert "학사 지침" in page and "검색 범위 공통 RAG" in page and 'id="libUploadModal"' in page
    c.post("/library/folders/scope", data={"key": key, "dept": "교무처"})
    with db._conn() as conn:
        did = conn.execute("SELECT doc_id FROM doc_folders").fetchone()[0]
    assert (db.get_document(did)["dept"], db.get_document(did)["access_level"]) == ("교무처", "dept")


def test_staff_cannot_pick_other_dept_or_change_folder_scope(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    r = c.post("/library/folders", data={"name": "몰래", "dept": "교무처"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert c.post("/library/folders/scope", data={"key": "f:1", "dept": "공통"}).status_code == 403
    assert c.post("/docs/scope", json={"ids": [1], "dept": "공통"}).status_code == 403


def test_upload_with_existing_or_new_program(tmp_path, monkeypatch):
    app, c = _dev(tmp_path)
    db = app.state.db
    from zzaimy.app import paths
    monkeypatch.setattr(paths, "platform_dir", lambda: tmp_path)
    r = c.post("/library/upload", data={"program": "_new", "new_program": "가나 인재양성 사업", "new_ministry": "고용노동부", "year": "2026"},
               files=[("files", ("계획.txt", io.BytesIO("본문".encode()), "text/plain"))], follow_redirects=False)
    assert "ok=" in r.headers["location"]
    import json
    led = json.loads((tmp_path / "kg_external.json").read_text(encoding="utf-8"))
    assert led["programs"][0]["name"] == "가나 인재양성 사업" and led["programs"][0]["ministry"] == "고용노동부"
    with db._conn() as conn:
        doc = dict(conn.execute("SELECT d.doc_type, p.program FROM documents d JOIN projects p ON p.id = d.project_id").fetchone())
    assert doc["doc_type"] == "grant" and doc["program"] == "program:가나인재양성사업|2026"
    # 같은 이름으로 다시 등록하면 장부에 새 항목을 만들지 않는다
    c.post("/library/upload", data={"program": "_new", "new_program": "가나 인재양성 사업"},
           files=[("files", ("결과.txt", io.BytesIO("본문".encode()), "text/plain"))])
    assert len(json.loads((tmp_path / "kg_external.json").read_text(encoding="utf-8"))["programs"]) == 1


def test_library_filters_format_sort_and_body(tmp_path, monkeypatch):
    app, c = _dev(tmp_path)
    db = app.state.db
    a = db.add_document("가 계획서.pdf", "x/a.pdf", doc_type="grant")
    b = db.add_document("나 결과보고서.hwp", "x/b.hwp", doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status = 'reviewed' WHERE id IN (?, ?)", (a, b))
    page = c.get("/?type=all&fmt=hwp").text
    assert "나 결과보고서" in page and "가 계획서" not in page
    page = c.get("/?type=all&sort=name").text
    assert page.index("가 계획서") < page.index("나 결과보고서")
    page = c.get("/?type=all&sort=old").text
    assert page.index("가 계획서") < page.index("나 결과보고서")
    assert 'name="body"' in page and 'name="fmt"' in page and 'name="since"' in page
