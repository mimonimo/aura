"""GPU 없는 서버(운영 VM)에서는 글자층 있는 PDF 의 MinerU 구조 추출을 기다리지 않는다 — 14일 동안 성공 0·시간 초과(5분) 5."""
import pytest

from zzaimy.app import pipeline


def test_no_gpu_reads_text_layer_without_mineru(monkeypatch, tmp_path):
    monkeypatch.setenv("ZZAIMY_MINERU_STRUCTURE", "0")
    processor = pipeline.DocumentProcessor()
    path = tmp_path / "규정.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(pipeline, "_format_mismatch", lambda _: None)
    monkeypatch.setattr(pipeline, "_vision_available", lambda: False)
    monkeypatch.setattr(processor, "_pdf_has_text_layer", lambda p, *a, **k: True)
    monkeypatch.setattr(processor, "_read_text_layer",
                        lambda p, min_pages=60: None if min_pages > 1 else ("제1조 목적", 3, [(1, "제1조 목적")]))
    monkeypatch.setattr(processor, "_parse_mineru", lambda *a, **k: pytest.fail("GPU 없는데 구조 추출을 부름"))
    assert processor._parse_inner(path) == "제1조 목적"
    assert "구조 추출 생략" in processor._last_parse_note


def test_structure_setting_and_gpu_probe(monkeypatch):
    monkeypatch.setenv("ZZAIMY_MINERU_STRUCTURE", "1")
    assert pipeline._mineru_structure_ok() is True
    monkeypatch.setenv("ZZAIMY_MINERU_STRUCTURE", "")
    pipeline._MINERU_OK.clear()
    monkeypatch.setattr("shutil.which", lambda name: None)              # nvidia-smi 없음 → 생략
    assert pipeline._mineru_structure_ok() is False
    pipeline._MINERU_OK.clear()
