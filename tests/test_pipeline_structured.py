

def test_overlay_text_layer_uses_original_chars(tmp_path):
    """디지털 PDF는 OCR 결과 대신 원본 텍스트 레이어의 글자를 쓴다."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas as rl_canvas

    from zzaimy.app.pipeline import DocumentProcessor
    from zzaimy.ingest.parsers.base import ParsedEntry, ParseResult

    pdf = tmp_path / "digital.pdf"
    try:
        pdfmetrics.getFont("HYSMyeongJo-Medium")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    cv = rl_canvas.Canvas(str(pdf), pagesize=(595, 842))
    cv.setFont("HYSMyeongJo-Medium", 14)
    cv.drawString(72, 770, "기초과학 지원 사업 안내")  # 페이지 상단
    cv.save()

    proc = DocumentProcessor.__new__(DocumentProcessor)
    # OCR이 "기추과하"로 오인식했다고 가정 — bbox는 페이지 포인트 좌표
    entry = ParsedEntry(
        page_no=1, kind="text", text="기추과하 지원 사업 안내",
        bbox=(60.0, 55.0, 400.0, 90.0),  # top 기준 y — 770pt는 top에서 72-90 근방
    )
    proc._last_result = ParseResult(parser="mineru", elapsed_s=0.0, entries=[entry])
    out = proc._overlay_text_layer(pdf)
    assert out is not None and "기초과학" in out
    assert "기추과하" not in out


def test_llm_correction_covers_table_cells(monkeypatch):
    """표 셀의 OCR 오타도 본문과 같은 교정을 거친다 — '주민등특번호' 사고 대응."""
    import json

    from zzaimy.app.pipeline import DocumentProcessor

    proc = DocumentProcessor.__new__(DocumentProcessor)
    fixes = {
        "주민등특번호": "주민등록번호", "등의여부": "동의여부",
        "본문 오타난 줄": "본문 고친 줄",
    }
    monkeypatch.setattr(
        DocumentProcessor, "_correct_texts",
        lambda self, texts: [fixes.get(t, t) for t in texts],
    )
    chunks = [
        {"kind": "text", "content": "본문 오타난 줄"},
        {"kind": "table", "content": json.dumps({
            "n_rows": 1, "n_cols": 2,
            "cells": [[0, 0, 1, 1, 1, "주민등특번호"], [0, 1, 1, 1, 1, "등의여부"]],
        }, ensure_ascii=False)},
    ]
    assert proc._llm_correct_chunks(chunks)
    assert chunks[0]["content"] == "본문 고친 줄"
    data = json.loads(chunks[1]["content"])
    assert data["cells"][0][5] == "주민등록번호"
    assert data["cells"][1][5] == "동의여부"


def test_downloaded_error_page_is_reported_as_such(tmp_path):
    """내려받기가 막혀 오류 쪽이 .pdf 로 저장된 파일 — 판독 실패가 아니라 그 이유를 말한다."""
    from zzaimy.app.pipeline import _format_mismatch

    bad = tmp_path / "공고.pdf"
    bad.write_bytes(b'<!DOCTYPE HTML PUBLIC "-//IETF"><HTML><TITLE>400 Bad Request</TITLE>')
    reason = _format_mismatch(bad)
    assert reason and "웹 페이지" in reason and "PDF" in reason

    ok = tmp_path / "정상.pdf"
    ok.write_bytes(b"%PDF-1.7 ...")
    assert _format_mismatch(ok) is None
    assert _format_mismatch(tmp_path / "그림.png") is None      # 검사 대상이 아닌 형식


def test_model_thinking_is_stripped_from_transcripts():
    from zzaimy.app.pipeline import _strip_think

    assert _strip_think("<think>이미지를 본다</think>\n푸른등대\n한국장학재단") == "푸른등대\n한국장학재단"
    assert _strip_think("사용자는 텍스트 추출을 요청했다.\n</think>\n\n푸른등대") == "푸른등대"
    assert _strip_think("푸른등대") == "푸른등대"
