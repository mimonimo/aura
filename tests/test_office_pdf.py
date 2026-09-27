"""사무 문서 열람 PDF — 반입 때 한 번 만들어 문서 폴더에 두고, 한글은 docx 를 거쳐 LibreOffice 가 그린다."""

from pathlib import Path

from zzaimy.app import office_pdf


class FakeDb:
    def __init__(self):
        self.files = []

    def add_file(self, kind, path, *, name="", doc_id=None, session_id=None, size=0):
        self.files.append((kind, path, name, doc_id, size)); return 1


def test_render_uses_converter_for_hwpx_then_soffice(tmp_path, monkeypatch):
    src = tmp_path / "2026-국고-0001 서식" / "원본.hwpx"
    src.parent.mkdir(); src.write_bytes(b"zip")
    seen = {}

    def fake_docx(path):
        seen["docx_src"] = Path(path); return b"DOCX", ".docx"

    def fake_to_pdf(stage, out_dir, timeout=0):
        seen["stage"] = stage.name; seen["stage_bytes"] = stage.read_bytes()
        out = out_dir / (stage.stem + ".pdf"); out.write_bytes(b"%PDF-1.4"); return out
    monkeypatch.setattr(office_pdf, "docx_for", fake_docx)
    monkeypatch.setattr(office_pdf, "to_pdf", fake_to_pdf)
    db = FakeDb()
    doc = {"id": 7, "stored_path": str(src)}
    out = office_pdf.render(db, doc)
    assert out == src.parent / "열람.pdf" and out.read_bytes() == b"%PDF-1.4"
    assert seen["stage"] == "원본.docx" and seen["stage_bytes"] == b"DOCX" and seen["docx_src"] == src
    assert db.files and db.files[0][0] == "view" and db.files[0][3] == 7
    # 두 번째는 만들지 않고 있는 것을 준다
    monkeypatch.setattr(office_pdf, "to_pdf", lambda *a, **k: (_ for _ in ()).throw(AssertionError("다시 만들면 안 된다")))
    assert office_pdf.render(db, doc) == out


def test_non_office_and_missing_soffice(tmp_path, monkeypatch):
    pdf = tmp_path / "원본.pdf"; pdf.write_bytes(b"x")
    assert office_pdf.render(FakeDb(), {"id": 1, "stored_path": str(pdf)}) is None
    xlsx = tmp_path / "원본.xlsx"; xlsx.write_bytes(b"x")
    monkeypatch.setattr(office_pdf, "soffice", lambda: None)
    assert office_pdf.render(FakeDb(), {"id": 2, "stored_path": str(xlsx)}) is None     # 렌더러가 없으면 조용히 없음
    assert office_pdf.is_office("a.hwp") and office_pdf.is_office("b.XLSX") and not office_pdf.is_office("c.pdf")


def test_sheet_pdf_preserves_whole_sheet_and_uses_new_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(office_pdf, "soffice", lambda: "/usr/bin/soffice")
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        (tmp_path / "sheet.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(office_pdf.subprocess, "run", run)
    for ext in (".xlsx", ".xls", ".ods"):
        src = tmp_path / ("sheet" + ext)
        assert office_pdf.to_pdf(src, tmp_path)
        assert 'SinglePageSheets' in commands[-1][commands[-1].index('--convert-to') + 1]
        assert office_pdf.view_path({'stored_path': str(src)}).name != office_pdf.VIEW_NAME
    office_pdf.to_pdf(tmp_path / "sheet.docx", tmp_path)
    assert commands[-1][commands[-1].index('--convert-to') + 1] == 'pdf'


def test_soffice_prefers_env_then_home_bundle(tmp_path, monkeypatch):
    exe = tmp_path / "opt" / "lo" / "opt" / "libreoffice25.8" / "program" / "soffice"
    exe.parent.mkdir(parents=True); exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("ZZAIMY_SOFFICE", raising=False)
    monkeypatch.setattr(office_pdf.Path, "home", classmethod(lambda cls: tmp_path))
    assert office_pdf.soffice() == str(exe)
    custom = tmp_path / "soffice"; custom.write_text("")
    monkeypatch.setenv("ZZAIMY_SOFFICE", str(custom))
    assert office_pdf.soffice() == str(custom)
