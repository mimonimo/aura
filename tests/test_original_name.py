"""원본 형식은 표시 이름(본문 제목)이 아니라 실제 파일에서 고른다.

반입하면 filename 이 본문 제목(확장자 없음)으로 바뀐다. 제목만 보고 형식을 고르면 한글 공고의 「원본 열기」가
미리보기 대신 내려받기로 넘어가고, 내려받은 파일에도 확장자가 없다. 합성 문서만 쓰고 변환기·DGX 는 부르지 않는다.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import archive, archive_original, office_pdf, storage
from zzaimy.app import original_preview as op
from zzaimy.app.main import create_app

TITLE = "2025년 인공지능(AI) 분야 첨단산업 인재양성 부트캠프 사업계획서 제출 안내"
DOTTED = "2. 도심 캠퍼스 안내"                          # Path.suffix 가 '. 도심 캠퍼스 안내' 를 확장자로 잘못 읽는 제목


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    return TestClient(app)


def _wait(c, url):
    for _ in range(200):
        if c.get(url).json()["state"] != "running":
            break
        time.sleep(0.02)
    return c.get(url).json()


def _disposition_name(r) -> str:
    cd = r.headers["content-disposition"]
    if "filename*=" in cd:
        return unquote(cd.split("filename*=", 1)[1].split("''", 1)[1].split(";")[0])
    return cd.split('filename="', 1)[1].split('"', 1)[0]


@pytest.mark.parametrize("ext,kind", [
    (".hwpx", "office"), (".hwp", "office"), (".docx", "office"), (".xlsx", "office"), (".pptx", "office"),
    (".pdf", "inline"), (".png", "inline"),
])
@pytest.mark.parametrize("title", [TITLE, DOTTED])
def test_titled_document_keeps_real_type(title, ext, kind):
    doc = {"filename": title, "stored_path": f"/data/documents/반입/2025/국고/Z-1 {title}/원본{ext}"}
    assert storage.original_ext(doc) == ext
    assert storage.original_name(doc) == title + ext
    assert op.kind_of(storage.original_name(doc)) == kind
    assert op.kind_of(doc) == kind                                  # 문서 기록을 그대로 넘겨도 같은 판단
    assert op.media_type(doc) == op.INLINE_TYPES.get(ext, "application/octet-stream")
    assert op.kind_of(title) == "download"                          # 제목만으로는 형식을 알 수 없다 — 그래서 제목을 넘기지 않는다


def test_original_name_edge_cases():
    assert storage.original_name({"filename": "보고서.PDF", "stored_path": "/x/원본.pdf"}) == "보고서.PDF"
    assert storage.original_name({"filename": "공고.pdf", "stored_path": "/x/원본.hwpx"}) == "공고.hwpx"   # 실제 파일이 이긴다
    assert storage.original_ext({"filename": "옛 이름.hwp", "stored_path": "/inbox/abc"}) == ".hwp"     # 확장자 없는 옛 저장 경로
    assert storage.original_ext({"filename": TITLE, "stored_path": ""}) == ""
    assert storage.original_name({"filename": "", "stored_path": "/x/원본.hwpx"}) == "원본.hwpx"
    assert storage.original_ext({"filename": TITLE, "stored_path": "dgx://링크/2025/v1.2 공고.HWPX"}) == ".hwpx"
    assert storage.original_ext({"filename": "v1.2", "stored_path": "/inbox/abc"}) == ""          # 숫자뿐인 꼬리는 확장자가 아니다


@pytest.mark.parametrize("stored", [
    "/data/documents/반입/2026/행정/Z-9 새 서식/원본.odg",          # 아직 쓰지 않는 형식
    "dgx://링크/2026/도면.odg",
    "nas://공유/2026/도면.ODG",                                     # 아직 없는 보관 위치(scheme://)
    "s3://bucket/a/b/도면.odg",
])
def test_unseen_type_and_location_still_keep_extension(stored):
    """목록에 없는 새 형식·새 보관 위치라도 실제 파일의 확장자를 그대로 쓴다 — 확장자 목록을 늘리지 않아도 된다."""
    doc = {"filename": DOTTED, "stored_path": stored}
    assert storage.original_ext(doc) == ".odg"
    assert storage.original_name(doc) == DOTTED + ".odg"
    assert op.kind_of(doc) == "download"                            # 보여 줄 수 없는 형식이면 내려받기 — 이름에는 확장자가 남는다


def test_unseen_extension_on_old_record_without_stored_suffix():
    doc = {"filename": "옛 도면.dwg", "stored_path": "/inbox/0a1b2c"}
    assert storage.original_ext(doc) == ".dwg" and storage.original_name(doc) == "옛 도면.dwg"


def _office_doc(db, tmp_path, ext=".hwpx", title=TITLE):
    src = tmp_path / "documents" / "반입" / f"Z-1 {title}" / f"원본{ext}"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"PK synthetic")
    did = db.add_document(title, str(src), doc_type="grant")
    return did, src


def test_office_original_previews_instead_of_downloading(client, tmp_path, monkeypatch):
    db = client.app.state.db
    did, src = _office_doc(db, tmp_path)
    monkeypatch.setattr(office_pdf, "soffice", lambda: "/usr/bin/soffice")
    seen = {}

    def fake_convert(path, target, progress=None, ext=""):
        seen["src"], seen["ext"] = Path(path), ext
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4 view")
        return target

    monkeypatch.setattr(office_pdf, "convert_file", fake_convert)
    r = client.get(f"/doc/{did}/original", follow_redirects=False)
    assert r.status_code == 200 and "/original/download" not in r.headers.get("location", "")
    assert "변환" in r.text                                          # 기다림 화면(사무 문서 단계)
    assert _wait(client, f"/doc/{did}/original/status")["state"] == "done"
    assert seen["src"] == src and seen["ext"] == ".hwpx"
    r = client.get(f"/doc/{did}/original", follow_redirects=False)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 view"
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    assert _disposition_name(r) == TITLE + ".pdf"


def test_download_keeps_extension(client, tmp_path):
    db = client.app.state.db
    did, _ = _office_doc(db, tmp_path, title=DOTTED)
    r = client.get(f"/doc/{did}/original/download")
    assert r.status_code == 200 and r.content == b"PK synthetic"
    assert r.headers["content-disposition"].startswith("attachment")
    assert _disposition_name(r) == DOTTED + ".hwpx"


def test_inline_titled_pdf_and_image(client, tmp_path):
    db = client.app.state.db
    for ext, body, mime in ((".pdf", b"%PDF-x", "application/pdf"), (".png", b"\x89PNG", "image/png")):
        src = tmp_path / f"원본{ext}"
        src.write_bytes(body)
        did = db.add_document(TITLE, str(src), doc_type="grant")
        r = client.get(f"/doc/{did}/original", follow_redirects=False)
        assert r.status_code == 200 and r.content == body and r.headers["content-type"] == mime
        assert _disposition_name(r) == TITLE + ext


def _dgx_doc(db, rel, title):
    did = db.add_document(title, f"dgx://{rel}", doc_type="grant", access_level="public")
    archive.ensure(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO archive_files (rel, size, mtime, removed_at) VALUES (?, ?, ?, '')", (rel, 12, 1))
    return did


def test_dgx_titled_office_is_converted_with_real_extension(client, tmp_path, monkeypatch):
    db = client.app.state.db
    did = _dgx_doc(db, "링크/2025/공고문.hwpx", DOTTED)
    fetched = tmp_path / "fetched.bin"                              # 받아 온 임시 파일에는 확장자가 없다
    fetched.write_bytes(b"PK synthetic")
    monkeypatch.setattr(archive_original, "fetch", lambda row: fetched)
    monkeypatch.setattr(office_pdf, "soffice", lambda: "/usr/bin/soffice")
    seen = {}

    def fake_convert(path, target, progress=None, ext=""):
        seen["suffix"] = Path(path).suffix
        target.write_bytes(b"%PDF-dgx")
        return target

    monkeypatch.setattr(office_pdf, "convert_file", fake_convert)
    r = client.get(f"/doc/{did}/original", follow_redirects=False)
    assert r.status_code == 200 and "/original/download" not in r.headers.get("location", "")
    assert _wait(client, f"/doc/{did}/original/status")["state"] == "done"
    assert seen["suffix"] == ".hwpx"                                 # 변환기는 실제 확장자로 형식을 고른다
    r = client.get(f"/doc/{did}/original")
    assert r.content == b"%PDF-dgx" and _disposition_name(r) == DOTTED + ".pdf"


def test_dgx_titled_pdf_preview_and_download(client, tmp_path, monkeypatch):
    db = client.app.state.db
    did = _dgx_doc(db, "링크/2025/보고서.pdf", TITLE)
    fetched = tmp_path / "fetched.bin"
    fetched.write_bytes(b"%PDF-dgx")
    monkeypatch.setattr(archive_original, "fetch", lambda row: fetched)
    client.get(f"/doc/{did}/original")
    assert _wait(client, f"/doc/{did}/original/status")["state"] == "done"
    r = client.get(f"/doc/{did}/original")
    assert r.headers["content-type"] == "application/pdf" and _disposition_name(r) == TITLE + ".pdf"
    r = client.get(f"/doc/{did}/original/download")
    assert r.content == b"%PDF-dgx" and _disposition_name(r) == TITLE + ".pdf"


def test_google_view_bytes_use_real_type(tmp_path):
    """「구글에서 열기」가 올리는 파일 — 이름은 제목+실제 확장자, 형식(mime·변환 목표)은 실제 파일에서."""
    from zzaimy.ingest import gdrive_files

    src = tmp_path / "원본.xlsx"
    src.write_bytes(b"PK sheet")
    data, name, mime, target = gdrive_files.bytes_for_view(None, {"id": 1, "filename": DOTTED, "stored_path": str(src)})
    assert data == b"PK sheet" and name == DOTTED + ".xlsx"
    assert (mime, target) == gdrive_files.CONVERT[".xlsx"]


def test_project_documents_report_real_format(client, tmp_path):
    db = client.app.state.db
    client.post("/projects", data={"name": "합성 프로젝트", "sector": "grant"})
    src = tmp_path / "원본.hwpx"
    src.write_bytes(b"PK synthetic")
    did = db.add_document(TITLE, str(src), doc_type="grant", project_id=1)
    sid = db.create_chat_session("합성 대화", project_id=1)
    rows = client.get(f"/api/chat/{sid}/documents").json()["documents"]
    row = next(r for r in rows if r["id"] == did)
    assert row["original_format"] == "hwpx"


def test_doc_page_spreadsheet_layout_from_real_type(client, tmp_path):
    db = client.app.state.db
    src = tmp_path / "원본.xlsx"
    src.write_bytes(b"PK sheet")
    did = db.add_document(DOTTED, str(src), doc_type="grant")
    db.update_document(did, status="reviewed", masked_text="표")
    assert "spreadsheet-extract" in client.get(f"/doc/{did}").text


def test_converter_gets_real_extension_when_file_name_lacks_it(tmp_path, monkeypatch):
    """확장자 없이 저장된 원본도 변환기에는 실제 확장자를 단 사본이 간다."""
    src = tmp_path / "abc"
    src.write_bytes(b"PK synthetic")
    seen = {}
    monkeypatch.setattr(office_pdf, "docx_for", lambda p: seen.setdefault("docx", Path(p).suffix) and None)

    def fake_to_pdf(stage, out_dir, timeout=0):
        seen["stage"] = Path(stage).suffix
        out = out_dir / "x.pdf"
        out.write_bytes(b"%PDF")
        return out

    monkeypatch.setattr(office_pdf, "to_pdf", fake_to_pdf)
    assert office_pdf.convert_file(src, tmp_path / "out.pdf", ext=".docx") == tmp_path / "out.pdf"
    assert seen == {"docx": ".docx", "stage": ".docx"}
