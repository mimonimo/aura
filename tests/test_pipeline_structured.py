

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
