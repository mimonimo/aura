

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


def test_public_documents_may_use_the_public_reader_only_when_set(tmp_path):
    from zzaimy.app.pipeline import DocumentProcessor
    from zzaimy.generate import llm_connections as lc

    lc.configure(tmp_path / "c.json")
    proc = DocumentProcessor()
    proc._doc_public = True
    assert proc._vision_role() == "vision"                    # 지정이 없으면 교내 판독
    ext = lc.add("클로드", "anthropic", "https://api.anthropic.com/v1/", "claude-sonnet-5", "sk-test")
    lc.set_role("vision_public", ext["id"], "claude-sonnet-5")
    assert proc._vision_role() == "vision_public"
    proc._doc_public = False
    assert proc._vision_role() == "vision"                    # 교내 문서는 언제나 교내 판독


def test_review_continues_when_the_model_hits_the_length_limit(monkeypatch):
    """검토 의견이 상한에 걸리면 한 번 이어 쓰고, 그래도 끝나지 않으면 끊겼다고 적는다."""
    from types import SimpleNamespace
    from zzaimy.app import pipeline as pl
    from zzaimy.app.pipeline import DocumentProcessor, looks_cut

    assert looks_cut("실무 역량 강화에 기여할") and not looks_cut("실무 역량 강화에 기여한다.")
    calls = []

    class FakeCompletions:
        def create(self, **kw):
            calls.append(kw)
            reasons = ["length", "stop"]
            content = ["요약: 편성표는 전공별로", " 교과목을 배치한다."][len(calls) - 1]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason=reasons[len(calls) - 1])])

    class FakeClient:
        model = "m"
        _extra = {}
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    monkeypatch.setattr("zzaimy.generate.client.VllmClient", lambda role="": FakeClient())
    out = DocumentProcessor()._review("본문", "regulation")
    assert out == "요약: 편성표는 전공별로\n교과목을 배치한다." and len(calls) == 2
    assert calls[0]["max_tokens"] == DocumentProcessor.REVIEW_MAX_TOKENS
    assert "이어서" in calls[1]["messages"][-1]["content"]


def test_missing_vision_model_is_written_into_the_parse_note():
    """판독 모델이 없으면 처리 기록에 남긴다 — 조용히 CPU OCR 로 떨어지지 않는다."""
    from zzaimy.app.pipeline import DocumentProcessor

    proc = DocumentProcessor()
    proc._last_parse_note = ""
    proc._note_no_vision(); proc._note_no_vision()
    assert proc._last_parse_note.count("판독 모델 없음") == 1
    proc._last_parse_note = "구조 추출 (MinerU)"
    proc._note_no_vision()
    assert proc._last_parse_note.startswith("구조 추출 (MinerU) · 판독 모델 없음")
