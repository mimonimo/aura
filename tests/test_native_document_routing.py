"""한글 원본은 PDF/OCR 경로에 진입하지 않는다. 실제 파서 품질 검사는 별도다."""

import pytest

from zzaimy.app import pipeline


@pytest.mark.parametrize("suffix,method", [
    (".hwp", "_parse_hwp_structured"),
    (".HWP", "_parse_hwp_structured"),
    (".hwpx", "_parse_hwpx_structured"),
    (".HWPX", "_parse_hwpx_structured"),
])
def test_hangul_uses_native_parser_before_ocr(monkeypatch, tmp_path, suffix, method):
    processor = pipeline.DocumentProcessor()
    path = tmp_path / f"원본{suffix}"
    # 형식 검증이 아닌 라우팅 검사이며 실제 문서나 외부 엔진을 사용하지 않는다.
    monkeypatch.setattr(pipeline, "_format_mismatch", lambda _: None)
    calls = []

    def native(source):
        calls.append(source)
        return "사업 목표 1,200명"

    def unexpected(*args, **kwargs):
        pytest.fail("한글 원본이 PDF/OCR 경로에 진입함")

    monkeypatch.setattr(processor, method, native)
    monkeypatch.setattr(processor, "_parse_mineru", unexpected)
    monkeypatch.setattr(processor, "_read_text_layer", unexpected)
    monkeypatch.setattr(pipeline, "_vision_available", unexpected)
    assert processor._parse_inner(path) == "사업 목표 1,200명"
    assert calls == [path]
    assert processor._ocr_used is False
