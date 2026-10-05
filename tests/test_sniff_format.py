"""확장자와 내용이 다른 원본 — 파일 머리로 실제 형식을 판별해 그 형식으로 읽는다."""
import zipfile

from zzaimy.app import pipeline


def test_sniff_zip_office_and_pdf(tmp_path):
    x = tmp_path / "a.hwp"
    with zipfile.ZipFile(x, "w") as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
    assert pipeline.sniff_suffix(x) == ".xlsx"
    h = tmp_path / "b.xlsx"
    with zipfile.ZipFile(h, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", "<s/>")
    assert pipeline.sniff_suffix(h) == ".hwpx"
    p = tmp_path / "c.xlsx"
    p.write_bytes(b"junk" * 10 + b"%PDF-1.7\n")
    assert pipeline.sniff_suffix(p) == ".pdf"
    n = tmp_path / "d.pdf"
    n.write_bytes(b"<html>blocked</html>")
    assert pipeline.sniff_suffix(n) is None


def test_mismatched_extension_is_read_as_real_format(tmp_path, monkeypatch):
    x = tmp_path / "보고서.xlsx"
    x.write_bytes(b"%PDF-1.7\n...")
    proc = pipeline.DocumentProcessor()
    seen = []
    real_inner = proc._parse_inner

    def fake_inner(path):
        if path.suffix == ".pdf":
            seen.append(path.name)
            return "본문"
        return real_inner(path)
    monkeypatch.setattr(proc, "_parse_inner", fake_inner)
    assert proc._parse_as(x, ".pdf", "XLSX 형식이 아닙니다") == "본문"
    assert seen == ["보고서.pdf"] and "실제 PDF" in proc._last_parse_note
