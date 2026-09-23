"""문서 저장 구조(ADR-0030) — DB 가 원본, 디스크는 종류별 정리 폴더. 반입·첨부·생성·삭제·이름 따라가기·이관을 검증한다."""

from pathlib import Path

from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor
from zzaimy.app import storage
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


def test_upload_lands_in_readable_document_folder_and_ledger(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    c.post("/upload", data={"doc_type": "grant"}, files={"file": ("2026 지역혁신 계획서: 초안?.pdf", b"%PDF", "application/pdf")})
    db = app.state.db
    d = db.get_document(1)
    p = Path(d["stored_path"])
    assert p.exists() and p.name == "원본.pdf"
    assert p.parent.parent.name == "국고" and p.parent.name.startswith(d["receipt_no"] + " 2026 지역혁신 계획서 초안")
    assert not list((tmp_path / "inbox").glob("*.pdf"))                 # inbox 에 남지 않는다
    files = db.list_files(doc_id=1)
    assert files and files[0]["kind"] == "intake" and files[0]["path"] == str(p)
    # 삭제하면 폴더째 사라지고 장부에서도 빠진다
    c.post("/doc/1/delete", follow_redirects=False)
    assert not p.parent.exists() and db.list_files(doc_id=1) == []


def test_criteria_and_ocr_uploads_use_type_folders(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    c.post("/criteria/upload", data={"sector": "common"}, files=[("file", ("학칙.pdf", b"%PDF", "application/pdf"))], follow_redirects=False)
    c.post("/ocr/upload", files=[("file", ("스캔.pdf", b"%PDF", "application/pdf"))], follow_redirects=False)
    kinds = {Path(d["stored_path"]).parent.parent.name for d in app.state.db.list_documents()}
    assert kinds == {"기준", "추출"}


def test_chat_attachment_goes_to_attachment_folder(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    c.post("/chat/send", data={"question": "검토해 줘"}, files={"attachment": ("계획서.pdf", b"%PDF", "application/pdf")}, follow_redirects=False)
    att = app.state.db.list_files(kind="attachment")
    assert len(att) == 1 and "첨부" in att[0]["path"] and att[0]["path"].endswith(" 계획서.pdf") and Path(att[0]["path"]).exists()


def test_rename_follows_title_and_generated_files_are_ledgered(tmp_path):
    db = Database(tmp_path / "t.db")
    src = tmp_path / "inbox" / "x.hwp"; src.parent.mkdir(); src.write_bytes(b"HWP")
    did = db.add_document("law01.hwp", str(src), doc_type="regulation")
    p = storage.adopt_original(db, did, src, base=tmp_path)
    assert p.parent.name.endswith(" law01")
    db.set_document_title(did, "복학원") if hasattr(db, "set_document_title") else db._conn().execute("UPDATE documents SET filename='복학원.hwp' WHERE id=?", (did,)).connection.commit()
    new = storage.rename_intake_dir(db, did, base=tmp_path)
    assert new is not None and new.name.endswith(" 복학원") and Path(db.get_document(did)["stored_path"]).exists()
    assert db.list_files(doc_id=did)[0]["path"].startswith(str(new))
    g = storage.save_generated(db, b"PK", ref=db.get_document(did)["receipt_no"], kind="초안", ext="docx", doc_id=did, base=tmp_path)
    assert g.exists() and "생성" in str(g) and db.list_files(kind="generated")[0]["doc_id"] == did
    assert storage.layout_summary(tmp_path)["intake"]["files"] == 1
    assert storage.safe_name('a/b:c*d?"e<f>g|h') == "a b c d e f g h"


def test_migration_moves_inbox_files_and_updates_db(tmp_path, monkeypatch):
    import importlib.util, sys
    db = Database(tmp_path / "t.db")
    inbox = tmp_path / "inbox"; inbox.mkdir()
    f = inbox / "abc123.pdf"; f.write_bytes(b"%PDF")
    imgs = inbox / "abc123_imgs"; imgs.mkdir(); (imgs / "p1.png").write_bytes(b"png")
    did = db.add_document("2026 공고.pdf", str(f), doc_type="grant")
    db.replace_doc_assets(did, [{"kind": "image", "page_no": 1, "path": str(imgs / "p1.png")}])
    (inbox / "chat_1.pdf").write_bytes(b"%PDF")
    (tmp_path / "weekly").mkdir(); (tmp_path / "weekly" / "2026-09-21.md").write_text("# 주간")
    spec = importlib.util.spec_from_file_location("mig", Path(__file__).resolve().parents[1] / "scripts" / "138_layout_migrate.py")
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)
    monkeypatch.setattr(sys, "argv", ["138", "--db", str(tmp_path / "t.db"), "--apply"])
    assert mig.main() == 0
    d = db.get_document(did)
    p = Path(d["stored_path"])
    assert p.name == "원본.pdf" and p.exists() and "반입" in str(p) and not f.exists()
    assert (p.parent / "원본_imgs" / "p1.png").exists()
    assert db.list_doc_assets(did)[0]["path"] == str(p.parent / "원본_imgs" / "p1.png")
    assert db.file_counts() == {"intake": 1, "attachment": 1, "report": 1}
    assert not (tmp_path / "weekly").exists() and (tmp_path / "documents" / "보고" / "주간" / "2026-09-21.md").exists()


def test_exports_keep_a_copy_in_generated_folder(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    c.post("/upload", data={"doc_type": "grant"}, files={"file": ("계획서.pdf", b"%PDF", "application/pdf")})
    db = app.state.db
    db.update_document(1, draft="## 1. 사업 개요\n지역 산업 수요에 맞춘다.", status="reviewed")
    assert c.get("/doc/1/draft.md").status_code == 200
    gen = db.list_files(kind="generated")
    assert gen and Path(gen[0]["path"]).exists() and "생성" in gen[0]["path"] and gen[0]["doc_id"] == 1
