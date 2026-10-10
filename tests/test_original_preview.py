"""원본 열기 — PDF·그림은 그대로, 사무 문서는 뒤에서 열람 PDF 로 바꾸며 기다림 화면, 두 번째부터는 바로. 내려받기는 따로.
구글에서 열기도 같은 기다림 화면(단계·지난 시간·실패 사유·다시 시도)을 쓴다. 변환기는 가짜로 바꿔 끼운다."""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import archive, archive_original, office_pdf, original_preview
from zzaimy.app.main import create_app


def _app(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"})
    return app, c


def _wait(c, url, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(url).json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("작업이 끝나지 않았다")


def test_pdf_and_image_open_inline_and_download_is_separate(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    pdf = tmp_path / "계획서.pdf"; pdf.write_bytes(b"%PDF-1.4 synthetic")
    did = db.add_document("계획서.pdf", str(pdf), doc_type="grant")
    r = c.get(f"/doc/{did}/original")
    assert r.status_code == 200 and r.content == pdf.read_bytes()
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    r = c.get(f"/doc/{did}/original/download")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    png = tmp_path / "사진.png"; png.write_bytes(b"\x89PNG synthetic")
    pid = db.add_document("사진.png", str(png), doc_type="ocr")
    r = c.get(f"/doc/{pid}/original")
    assert r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    other = tmp_path / "묶음.zip"; other.write_bytes(b"PK synthetic")
    zid = db.add_document("묶음.zip", str(other), doc_type="grant")
    r = c.get(f"/doc/{zid}/original", follow_redirects=False)       # 브라우저가 못 보는 형식은 내려받기로
    assert r.status_code == 303 and r.headers["location"] == f"/doc/{zid}/original/download"


def test_hwp_is_converted_in_background_then_cached(tmp_path, monkeypatch):
    app, c = _app(tmp_path)
    db = app.state.db
    folder = tmp_path / "문서" / "2026-국고-0001 서식"; folder.mkdir(parents=True)
    src = folder / "원본.hwp"; src.write_bytes(b"HWP synthetic")
    did = db.add_document("서식.hwp", str(src), doc_type="grant")
    gate = threading.Event()
    calls = []

    def fake_docx(path):
        calls.append("docx"); gate.wait(5); return b"DOCX", ".docx"

    def fake_to_pdf(stage, out_dir, timeout=0):
        calls.append("pdf"); out = out_dir / (stage.stem + ".pdf"); out.write_bytes(b"%PDF-1.4 view"); return out
    monkeypatch.setattr(office_pdf, "docx_for", fake_docx)
    monkeypatch.setattr(office_pdf, "to_pdf", fake_to_pdf)
    monkeypatch.setattr(office_pdf, "soffice", lambda: "/usr/bin/soffice")

    r = c.get(f"/doc/{did}/original")
    page = r.text
    assert r.status_code == 200 and "원본 미리보기를 준비하고 있습니다" in page and "서식.hwp" in page
    for label in ("원본 받기", "변환", "미리보기 만들기", "문서 보기로 돌아가기", "원본 내려받기"):
        assert label in page
    assert f'href="/doc/{did}"' in page and f"/doc/{did}/original/download" in page
    time.sleep(0.1)
    st = c.get(f"/doc/{did}/original/status").json()
    assert st["state"] == "running" and st["step"] == "convert" and "elapsed" in st
    c.get(f"/doc/{did}/original")                                    # 기다리는 동안 다시 눌러도 작업은 하나
    gate.set()
    st = _wait(c, f"/doc/{did}/original/status")
    assert st["state"] == "done" and st["url"] == f"/doc/{did}/original" and st["step"] == "render"
    assert calls == ["docx", "pdf"]
    r = c.get(f"/doc/{did}/original")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 view" and r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers["content-disposition"] and ".pdf" in r.headers["content-disposition"]
    assert office_pdf.view_path(db.get_document(did)).is_file()        # 문서 화면 「원문」 탭도 같은 PDF 를 쓴다
    monkeypatch.setattr(office_pdf, "to_pdf", lambda *a, **k: (_ for _ in ()).throw(AssertionError("다시 만들면 안 된다")))
    assert c.get(f"/doc/{did}/original").content == b"%PDF-1.4 view"
    r = c.get(f"/doc/{did}/original/download")                      # 원본 파일 그대로
    assert r.content == b"HWP synthetic" and r.headers["content-disposition"].startswith("attachment")


def test_conversion_failure_shows_reason_and_retry(tmp_path, monkeypatch):
    app, c = _app(tmp_path)
    db = app.state.db
    src = tmp_path / "표.xlsx"; src.write_bytes(b"synthetic")
    did = db.add_document("표.xlsx", str(src), doc_type="grant")
    monkeypatch.setattr(office_pdf, "soffice", lambda: None)
    c.get(f"/doc/{did}/original")
    st = _wait(c, f"/doc/{did}/original/status")
    assert st["state"] == "error" and "LibreOffice" in st["msg"] and st["step"] == "fetch"   # 변환기가 없으면 원본을 받기 전에 알린다
    page = c.get(f"/doc/{did}/original").text                      # 다시 시도 — 새 작업을 띄우고 화면을 준다
    assert "다시 시도" in page and f'href="/doc/{did}/original"' in page
    st = _wait(c, f"/doc/{did}/original/status")
    assert st["state"] == "error"
    monkeypatch.setattr(office_pdf, "soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(office_pdf, "to_pdf", lambda *a, **k: None)
    c.get(f"/doc/{did}/original")
    st = _wait(c, f"/doc/{did}/original/status")
    assert st["state"] == "error" and "미리보기 PDF 를 만들지 못했습니다" in st["msg"]
    page = c.get(f"/doc/{did}/original").text
    assert "원본 내려받기" in page


def test_dgx_hwp_is_fetched_converted_into_cache(tmp_path, monkeypatch):
    app, c = _app(tmp_path)
    db = app.state.db
    rel = "사업/2025/큰 계획서.hwp"
    did = db.add_document("큰 계획서.hwp", "dgx://" + rel, doc_type="grant", access_level="public")
    archive.ensure(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO archive_files (rel, size, mtime, removed_at) VALUES (?, ?, ?, '')", (rel, 60_000_000, 7))
    fetched = tmp_path / "zzaimy-original-abc"

    def fake_fetch(row):
        fetched.write_bytes(b"HWP big synthetic"); return fetched
    seen = {}

    def fake_convert(src, target, progress=None):
        seen["suffix"] = src.suffix
        progress("convert"); progress("render")
        target.write_bytes(b"%PDF dgx"); return target
    monkeypatch.setattr(archive_original, "fetch", fake_fetch)
    monkeypatch.setattr(office_pdf, "convert_file", fake_convert)
    monkeypatch.setattr(office_pdf, "soffice", lambda: "/usr/bin/soffice")
    c.get(f"/doc/{did}/original")
    st = _wait(c, f"/doc/{did}/original/status")
    assert st["state"] == "done" and seen["suffix"] == ".hwp"           # 변환기는 확장자를 보고 고른다
    assert not fetched.exists() and not fetched.with_suffix(".hwp").exists()   # 받아 온 임시 원본은 지운다
    cached = list(original_preview.cache_dir(tmp_path).glob(f"{did}-*.pdf"))
    assert len(cached) == 1
    r = c.get(f"/doc/{did}/original")
    assert r.content == b"%PDF dgx" and r.headers["content-type"] == "application/pdf"
    assert archive_original.transfer_timeout(60_000_000) > 45            # 큰 원본은 받기 제한 시간을 늘린다


def test_cache_prune_keeps_newest(tmp_path):
    d = tmp_path / "preview"; d.mkdir()
    old = d / "1-a.pdf"; old.write_bytes(b"x" * 60)
    new = d / "2-b.pdf"; new.write_bytes(b"x" * 60)
    import os
    os.utime(old, (1, 1))
    original_preview.prune(d, limit=100, keep=new)
    assert not old.exists() and new.exists()


def test_google_view_uses_shared_wait_page_with_steps(tmp_path, monkeypatch):
    from zzaimy.ingest import gdrive_files

    app, c = _app(tmp_path)
    db = app.state.db
    src = tmp_path / "보고.hwp"; src.write_bytes(b"HWP synthetic")
    did = db.add_document("보고.hwp", str(src), doc_type="grant")
    gate = threading.Event()
    monkeypatch.setattr(gdrive_files, "account_for", lambda *a, **k: "staff@ync.ac.kr")
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda email: True)
    monkeypatch.setattr(gdrive_files, "project_folder_for", lambda *a, **k: "folder-1")

    def fake_copy(db_, doc, email, folder, http=None, refresh=False, progress=None):
        progress("convert"); progress("upload"); gate.wait(5)
        return {"url": "https://docs.google.com/document/d/x/edit"}
    monkeypatch.setattr(gdrive_files, "google_copy", fake_copy)
    r = c.get(f"/doc/{did}/view", follow_redirects=False)
    assert r.status_code == 200 and "구글에서 열 준비를 하고 있습니다" in r.text
    for label in ("원본 받기", "변환", "구글에 올리기", "문서 보기로 돌아가기"):
        assert label in r.text
    assert f"/doc/{did}/view/status" in r.text
    time.sleep(0.1)
    assert c.get(f"/doc/{did}/view/status").json()["step"] == "upload"
    gate.set()
    st = _wait(c, f"/doc/{did}/view/status")
    assert st["state"] == "done" and st["url"].startswith("https://docs.google.com/")

    def broken(*a, **k):
        raise RuntimeError("드라이브 응답 없음")
    monkeypatch.setattr(gdrive_files, "google_copy", broken)
    other = db.add_document("다른.hwp", str(src), doc_type="grant")
    c.get(f"/doc/{other}/view", follow_redirects=False)
    st = _wait(c, f"/doc/{other}/view/status")
    assert st["state"] == "error" and "드라이브 응답 없음" in st["msg"] and st["step"] == "fetch"
    page = c.get(f"/doc/{other}/view", follow_redirects=False).text
    assert "다시 시도" in page and f'href="/doc/{other}/view"' in page


def test_doc_page_offers_open_and_separate_download(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    src = tmp_path / "a.hwp"; src.write_bytes(b"x")
    did = db.add_document("a.hwp", str(src), doc_type="grant")
    page = c.get(f"/doc/{did}").text
    assert f'href="/doc/{did}/original"' in page and f'href="/doc/{did}/original/download"' in page
    assert "내려받기" in page
