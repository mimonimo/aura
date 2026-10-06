"""목차 요약 양식은 기존 CLI 옵션으로도 Drive에 게시되지 않는다."""
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("options", [[], ["--common"], ["--dir", "custom", "--folder", "custom"]])
def test_legacy_publish_stops_before_drive_calls(monkeypatch, capsys, options):
    from zzaimy.ingest import gdrive, gdrive_files

    def forbidden(*args, **kwargs):
        pytest.fail("폐기된 게시 경로는 Drive를 호출하면 안 됩니다")

    monkeypatch.setattr(gdrive, "_http", forbidden)
    for name in ("ensure_folder", "find_in_folder", "upload_file"):
        monkeypatch.setattr(gdrive_files, name, forbidden)
    path = Path(__file__).parents[1] / "scripts" / "169_upload_templates.py"
    spec = importlib.util.spec_from_file_location("legacy_template_publish", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path), "--email", "test@example.com", *options])

    assert module.main() == 2
    assert "게시 중단" in capsys.readouterr().err
