"""MinerU middle.json의 줄 단위 OCR 좌표 추출 — 스캔 문서 투명 레이어의 재료."""


def test_extract_middle_lines():
    from zzaimy.ingest.parsers.mineru import extract_middle_lines

    middle = {"pdf_info": [{
        "page_idx": 0,
        "page_size": [720, 540],
        "preproc_blocks": [
            {"type": "title", "lines": [
                {"bbox": [181, 133, 533, 178], "spans": [
                    {"bbox": [181, 133, 533, 178], "type": "text",
                     "content": "고졸 후학습자 장학금", "score": 0.947}]}]},
            {"type": "text", "lines": [
                {"bbox": [50, 200, 300, 220], "spans": [
                    {"bbox": [50, 200, 160, 220], "type": "text",
                     "content": "지원 대상", "score": 0.91},
                    {"bbox": [170, 200, 300, 220], "type": "text",
                     "content": "재직자", "score": 0.88}]}]},
            {"type": "image", "lines": []},
        ],
        "discarded_blocks": [
            {"type": "discarded", "lines": [
                {"bbox": [1, 1, 5, 5], "spans": [
                    {"type": "text", "content": "쓰레기", "score": 0.2}]}]}],
    }]}
    lines, sizes = extract_middle_lines(middle)
    assert sizes == {1: (720.0, 540.0)}
    assert len(lines) == 2                      # discarded 제외, 이미지 블록 무시
    assert lines[0]["page_no"] == 1
    assert lines[0]["content"] == "고졸 후학습자 장학금"
    assert lines[0]["bbox"] == "181.0,133.0,533.0,178.0"
    assert lines[1]["content"] == "지원 대상 재직자"   # 같은 줄 스팬 결합


def test_extract_middle_lines_drops_low_score():
    from zzaimy.ingest.parsers.mineru import extract_middle_lines

    middle = {"pdf_info": [{
        "page_idx": 0, "page_size": [700, 500],
        "preproc_blocks": [{"type": "text", "lines": [
            {"bbox": [10, 10, 90, 30], "spans": [
                {"bbox": [10, 10, 90, 30], "type": "text",
                 "content": "노이즈", "score": 0.2}]}]}],
    }]}
    lines, _ = extract_middle_lines(middle)
    assert lines == []


def test_scale_ocr_lines_to_page_points():
    """middle.json 좌표계 → PDF 포인트 좌표계 변환."""
    from zzaimy.app.pdf_lines import scale_ocr_lines

    payload = {
        "page_sizes": {"1": [720.0, 540.0]},
        "lines": [
            {"page_no": 1, "kind": "text", "content": "제목",
             "bbox": "180.0,135.0,540.0,180.0"},
            {"page_no": 2, "kind": "text", "content": "치수 없는 쪽",
             "bbox": "10,10,20,20"},
        ],
    }
    out = scale_ocr_lines(payload, {1: (595.0, 842.0)})
    assert len(out) == 1                       # 치수를 모르는 쪽은 버린다
    x0, y0, x1, y1 = (float(v) for v in out[0]["bbox"].split(","))
    assert abs(x0 - 180.0 * 595 / 720) < 0.2
    assert abs(y1 - 180.0 * 842 / 540) < 0.2
    assert out[0]["justify"] is True


def test_image_layout_from_lines():
    """사진 문서 — 줄 좌표 payload를 그대로 배치 항목·페이지 치수로 변환."""
    from zzaimy.app.pdf_lines import image_layout_from_lines

    payload = {
        "page_sizes": {"1": [1600.0, 1200.0]},
        "lines": [
            {"page_no": 1, "kind": "text", "content": "행사 안내",
             "bbox": "100.0,80.0,700.0,140.0"},
        ],
    }
    items, sizes = image_layout_from_lines(payload)
    assert sizes == {1: (1600.0, 1200.0)}
    assert len(items) == 1 and items[0]["justify"] is True
    assert items[0]["bbox"] == "100.0,80.0,700.0,140.0"
