"""원격·DB·Drive 없이 원본 서식 준비의 실패/보존 계약을 확인한다."""
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def stage(monkeypatch, tmp_path):
    import dotenv
    from zzaimy.app import archive_original, db
    from zzaimy.ingest import hwp5_docx, hwpx_docx

    path = Path(__file__).parents[1] / "scripts/stage_source_templates.py"
    spec = importlib.util.spec_from_file_location("stage_source_templates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a: None)
    temporary = tmp_path / "download.hwp"
    temporary.write_bytes(b"original source")
    row = {"rel": "business/form.hwp", "size": 15, "mtime": 1}
    query = Mock(return_value=SimpleNamespace(fetchone=lambda: row))
    database = Mock(return_value=SimpleNamespace(
        get_document=lambda ident: {"filename": "form.hwp"},
        _conn=lambda: nullcontext(SimpleNamespace(execute=query)),
    ))
    fetch = Mock(return_value=temporary)
    hwp = Mock()
    hwpx = Mock(side_effect=AssertionError("다른 변환기로 폴백 금지"))
    monkeypatch.setattr(db, "Database", database)
    monkeypatch.setattr(archive_original, "fetch", fetch)
    monkeypatch.setattr(hwp5_docx, "convert", hwp)
    monkeypatch.setattr(hwpx_docx, "convert", hwpx)
    out = tmp_path / "staged"
    monkeypatch.setattr(sys, "argv", [str(path), "--doc", "42", "--out", str(out)])
    return SimpleNamespace(module=module, out=out, temporary=temporary, row=row,
                           database=database, fetch=fetch, hwp=hwp, hwpx=hwpx)


def test_existing_output_is_not_reused(stage):
    stage.out.mkdir()
    marker = stage.out / "existing.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        stage.module.main()
    assert marker.read_text() == "keep"
    stage.database.assert_not_called()
    stage.fetch.assert_not_called()


def test_hwp_conversion_failure_does_not_fallback(stage):
    stage.hwp.side_effect = RuntimeError("conversion_failed")
    with pytest.raises(RuntimeError, match="conversion_failed"):
        stage.module.main()
    stage.hwpx.assert_not_called()
    assert not stage.temporary.exists()
    assert (stage.out / "42/original.hwp").read_bytes() == b"original source"
    assert not (stage.out / "42/working.docx").exists()
    assert not (stage.out / "42/report.json").exists()


def test_temporary_download_removed_when_copy_fails(stage, monkeypatch):
    def failed_copy(*args):
        raise OSError("copy_failed")
    monkeypatch.setattr(stage.module.shutil, "copyfile", failed_copy)
    with pytest.raises(OSError, match="copy_failed"):
        stage.module.main()
    assert not stage.temporary.exists()
    stage.hwp.assert_not_called()


def test_invalid_conversion_is_not_published_as_working_docx(stage):
    stage.hwp.return_value = (b"not a docx", {})
    with pytest.raises(zipfile.BadZipFile):
        stage.module.main()
    assert not (stage.out / "42/working.docx").exists()
    assert not (stage.out / "42/report.json").exists()
    assert not stage.temporary.exists()


def test_valid_conversion_records_structure_but_not_quality_approval(stage):
    xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Heading</w:t></w:r></w:p><w:tbl>
      <w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>
      <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>
      <w:p><w:r><w:t>Input</w:t></w:r></w:p></w:tc></w:tr>
      </w:tbl></w:body></w:document>'''
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml)
    content = stream.getvalue()
    stage.hwp.return_value = (content, {"tables": 1})

    stage.module.main()

    report = json.loads((stage.out / "42/report.json").read_text())
    assert report["table_shapes"] == [{"rows": 1, "grid_columns": 2}]
    assert report["table_count"] == report["horizontal_merges"] == report["vertical_merges"] == 1
    assert report["source_sha256"] == hashlib.sha256(b"original source").hexdigest()
    assert report["docx_sha256"] == hashlib.sha256(content).hexdigest()
    assert report["quality"] == "pending_visual_and_google_roundtrip"
    assert (stage.out / "42/working.docx").read_bytes() == content
    assert not stage.temporary.exists()
    stage.hwp.assert_called_once_with(stage.out / "42/original.hwp", line_rule="atLeast")
    stage.hwpx.assert_not_called()
