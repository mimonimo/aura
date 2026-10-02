import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def review():
    spec = importlib.util.spec_from_file_location("visual_review", Path(__file__).parents[1] / "scripts/172_ocr_visual_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_preserves_page_source_and_failures(review, tmp_path):
    from PIL import Image

    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"synthetic source, not real PDF")
    seen = []

    def render(pdf, pages, work):
        assert pages == [2]
        image = work / "p3.png"
        Image.new("RGB", (100, 140), "white").save(image)
        return [image], "목표 100명 / 실적 80명"

    def engine(name, images, scan, work):
        seen.append((name, images[0].read_bytes()))
        if name == "docling":
            raise RuntimeError("synthetic failure <script>bad</script>")
        return "목표 100명\n<script>alert(1)</script>"

    fake = SimpleNamespace(render=render, run_engine=engine,
                           cer68=SimpleNamespace(images_to_pdf=lambda images, path: None))
    data = review.collect(pdf, [3], ["mineru", "docling"], "synthetic only", fake)
    assert data["pages"][0]["number"] == 3
    assert seen[0][1] == seen[1][1]
    assert len(data["source_sha256"]) == 64
    assert data["metadata"]["review_status"] == "pending"
    assert data["pages"][0]["results"][1]["error"].startswith("RuntimeError")
    html = review.report_html(data)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "data:image/png;base64," in html
    assert "docling · 실패" in html
    assert "원본 3쪽" in html


def test_invalid_page_rejected_before_output_creation(review, tmp_path, monkeypatch):
    import sys
    out = tmp_path / "result"
    monkeypatch.setattr(sys, "argv", ["172", "--pdf", "missing.pdf", "--pages", "0",
                                     "--settings-note", "test", "--out", str(out)])
    with pytest.raises(SystemExit) as error:
        review.main()
    assert error.value.code == 2
    assert not out.exists()
