"""추출 고도화 — 첫 쪽 머리 영역 보존과 줄 끝 잘림 방지 (합성 자료만)."""
import json
import subprocess

from zzaimy.app.pdf_lines import complete_line_rect


def _row(x0, y, n, w=8.0, gap=1.0):
    out, x = [], x0
    for _ in range(n):
        out.append((x, y, x + w, y + 9))
        x += w + gap
    return out


def test_line_is_completed_past_a_narrow_block_box():
    boxes = _row(100, 700, 20)                      # 한 줄 20글자
    rect = (99, 699, 140, 710)                      # 앞 4글자만 덮는 좁은 상자
    left, _, right, _ = complete_line_rect(boxes, rect)
    assert left <= 100 and right >= boxes[-1][2]


def test_completion_does_not_jump_the_column_gap():
    left_col = _row(60, 700, 10)
    right_col = _row(320, 700, 10)                  # 단 사이 큰 틈
    rect = (59, 699, 90, 710)
    _, _, right, _ = complete_line_rect(left_col + right_col, rect)
    assert right < 320


def test_first_page_header_blocks_are_kept_as_title(tmp_path, monkeypatch):
    from zzaimy.ingest.parsers import mineru as m

    out = tmp_path / "out"
    (out / "doc").mkdir(parents=True)
    content = [
        {"type": "text", "page_idx": 0, "text": "제 1 장 총 칙", "text_level": 1, "bbox": [390, 183, 600, 203]},
        {"type": "header", "page_idx": 0, "text": "영남이공대학교 산학협력단 사무분장 규정", "bbox": [140, 82, 454, 102]},
        {"type": "header", "page_idx": 0, "text": "학과장회 통과일자 : 2022년 05월 26일", "bbox": [333, 126, 539, 140]},
        {"type": "page_number", "page_idx": 0, "text": "10-03-1", "bbox": [277, 803, 316, 815]},
        {"type": "header", "page_idx": 1, "text": "영남이공대학교 산학협력단 사무분장 규정", "bbox": [140, 40, 454, 60]},
        {"type": "text", "page_idx": 1, "text": "24. 교직원 상벌에 관한 업무", "bbox": [100, 90, 500, 110]},
    ]
    (out / "doc" / "doc_content_list.json").write_text(json.dumps(content, ensure_ascii=False))
    monkeypatch.setattr(m.MineruParser, "_cli", staticmethod(lambda: "mineru"))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    res = m.MineruParser().parse(pdf, work_dir=out)
    texts = [(e.page_no, e.kind, e.text) for e in res.entries]
    assert texts[0] == (1, "heading", "영남이공대학교 산학협력단 사무분장 규정")
    assert texts[1] == (1, "text", "학과장회 통과일자 : 2022년 05월 26일")
    assert sum(1 for t in texts if "사무분장 규정" in t[2]) == 1     # 2쪽 반복 머리말은 버린다
    assert not any("10-03-1" in t[2] for t in texts)


def test_digital_table_json_is_not_flagged_as_ocr_damage():
    import json

    from zzaimy.app.chunk_quality import ocr_damage_signals

    cells = [[0, 0, 1, 1, 1, "연번"], [0, 1, 1, 1, 1, "구분"], [1, 0, 1, 1, 0, "1"], [1, 1, 1, 1, 0, "계"],
             [2, 0, 1, 1, 0, "2"], [2, 1, 1, 1, 0, "예"], [3, 0, 1, 1, 0, "3"], [3, 1, 1, 1, 0, "○"],
             [4, 1, 1, 1, 0, "가"], [5, 1, 1, 1, 0, "나"]]
    assert ocr_damage_signals(json.dumps({"n_rows": 6, "n_cols": 2, "cells": cells}, ensure_ascii=False)) == []
    # 표가 아닌 글에서 글자가 하나씩 흩어지면 여전히 잡는다
    assert "글자 단위 분해" in ocr_damage_signals("학 과 장 회 통 과 일 자 는 이 렇 다 고 한 다")
    assert ocr_damage_signals("제 1 장 총 칙") == []           # 자간 띄운 짧은 제목은 정상 조판


def test_bullet_style_korean_is_not_syllable_split():
    from zzaimy.app.chunk_quality import ocr_damage_signals

    text = ("Ⅰ. 추진 배경 및 경과 □ 추진 배경 ◦ 디지털 전환, 글로벌 경쟁 심화에 따라 신기술 초격차 확보 및 "
            "급증하는 신산업 인력 수요에 대응 ◦ 개별대학 자원만으로는 신기술·신산업 분야의 융·복합적 특성 및 "
            "불확실성 등에 대응한 효과적인 인재 양성에 한계 ⇒ 각 대학 간 자원 을 공동 활용 하여 교육과정 개발")
    assert "글자 단위 분해" not in ocr_damage_signals(text)
    broken = "첨 단 신 소 재 분야 이 차 전 지 및 차 세 대 통 신 그리고 바 이 오 헬 스 항 공 드 론 분 야"
    assert "글자 단위 분해" in ocr_damage_signals(broken)


def test_table_text_does_not_repeat_column_merged_cells():
    from zzaimy.app.render import render_table_text

    data = {"n_rows": 3, "n_cols": 3, "cells": [
        [0, 0, 1, 3, 1, "사업 본 신청서"],                 # 가로 병합
        [1, 0, 2, 1, 1, "구분"], [1, 1, 1, 1, 0, "○"], [1, 2, 1, 1, 0, "○"],   # 세로 병합 + 같은 값 다른 칸
        [2, 1, 1, 1, 0, "가"], [2, 2, 1, 1, 0, "나"]]}
    lines = render_table_text(data).split("\n")
    assert lines[0] == "사업 본 신청서"
    assert lines[1] == "구분 | ○ | ○"                     # 다른 칸의 같은 값은 그대로
    assert lines[2] == "구분 | 가 | 나"                   # 세로 병합은 아래 행에도 채운다


def test_table_text_skips_rows_that_only_repeat_merged_cells():
    """서식 표: 세로 병합 라벨 아래에 새 글이 없는 행은 되풀이하지 않는다(실측 2026-09-22 복학원)."""
    from zzaimy.app.render import render_table_text

    data = {"n_rows": 3, "n_cols": 4, "cells": [
        [0, 0, 3, 1, 1, "군제대자에 한함"], [0, 1, 3, 2, 1, "복학원"], [0, 3, 1, 1, 0, "담당"],
        [1, 3, 1, 1, 0, "전결"],
        [2, 3, 1, 1, 0, ""]]}
    lines = render_table_text(data).split("\n")
    assert lines == ["군제대자에 한함 | 복학원 | 담당", "군제대자에 한함 | 복학원 | 전결"]


def test_digital_pdf_is_never_sent_to_vision(tmp_path, monkeypatch):
    """글자층이 있는 PDF 는 원문을 바로 읽는다 — 비전 판독용 쪽 그림을 만들지 않는다."""
    from zzaimy.app.pipeline import DocumentProcessor

    pdf = tmp_path / "digital.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(DocumentProcessor, "_pdf_has_text_layer", staticmethod(lambda p, **k: True))
    assert DocumentProcessor._pdf_to_images(pdf) == []


def test_vision_is_enabled_when_ollama_model_can_see(monkeypatch):
    import io
    import json
    import urllib.request

    from zzaimy.generate import client as cl

    monkeypatch.setattr(cl, "is_ollama", lambda url: True)
    cl._VISION_CACHE.clear()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5: io.BytesIO(json.dumps({"capabilities": ["completion", "vision"]}).encode()))
    assert cl.model_can_see("http://dgx:11434/v1", "qwen3.6:35b") is True
    cl._VISION_CACHE.clear()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5: io.BytesIO(json.dumps({"capabilities": ["completion"]}).encode()))
    assert cl.model_can_see("http://dgx:11434/v1", "gpt-oss:120b") is False


def test_near_duplicate_chunks_take_one_slot():
    from zzaimy.app.regulations import drop_near_duplicates

    a = {"id": 1, "content": "제3조(임용권자) 계약직원은 산학협력단장이 임용한다. 다만 필요한 경우 위임할 수 있다."}
    b = {"id": 2, "content": "제3조(임용권자)  계약직원은 산학협력단장이 임용한다. 다만 필요한 경우 위임할 수 있다."}
    c = {"id": 3, "content": "제4조(계약기간) 계약기간은 1년 이내로 한다."}
    assert [x["id"] for x in drop_near_duplicates([a, b, c])] == [1, 3]


def test_bbox_mapper_detects_normalized_coordinates():
    """0~1000 정규화 bbox 를 배율 하나로 나누면 윗줄을 떠 온다 — 페이지별로 맞는 변환을 고른다."""
    from types import SimpleNamespace

    from zzaimy.app.pdf_lines import page_bbox_mappers

    pw, ph = 595.0, 842.0
    lines = [(80, 100, "영남이공대학교 산학협력단 사무분장 규정"), (120, 140, "학과장회 통과일자 : 2022년 05월 26일"),
             (180, 200, "제 1 장 총 칙")]

    class TP:
        def get_text_bounded(self, left, bottom, right, top):
            y0, y1 = ph - top, ph - bottom
            return " ".join(t for a, b, t in lines if y0 <= (a + b) / 2 <= y1)

    def norm(x0, y0, x1, y1):
        return (x0 / pw * 1000, y0 / ph * 1000, x1 / pw * 1000, y1 / ph * 1000)

    ents = [SimpleNamespace(page_no=1, kind="heading", text=lines[0][2], bbox=norm(140, 80, 560, 100)),
            SimpleNamespace(page_no=1, kind="text", text=lines[1][2], bbox=norm(330, 120, 560, 140)),
            SimpleNamespace(page_no=1, kind="heading", text=lines[2][2], bbox=norm(230, 180, 360, 200))]
    fits = {1: max(max(e.bbox[2] / pw, e.bbox[3] / ph) for e in ents)}
    m = page_bbox_mappers(ents, lambda pg: TP(), {1: (pw, ph)}, fits)
    x0, y0, x1, y1 = m[1](ents[1].bbox)
    assert abs(y0 - 120) < 1 and abs(y1 - 140) < 1


def test_lattice_cell_text_restores_word_gaps():
    from zzaimy.ingest.parsers.lattice import _cell_text

    def run(word, x, gap_after=0.0):
        out = []
        for ch in word:
            out.append((x + 4.5, 100.0, 10.0, ch, 4.5))
            x += 9.0 + 0.2                                  # 자간 2%
        return out, x + gap_after
    a, x = run("도제학교", 0.0, gap_after=3.0)              # 낱말 간격 30%
    b, _ = run("운영지원", x)
    assert _cell_text(a + b, 0, 200, 90, 110) == "도제학교 운영지원"


def test_text_layer_pages_keep_page_numbers_on_chunks():
    """글자층 직독 문서의 조각에는 쪽 번호가 붙는다(실측 2026-09-22: 250쪽 문서 조각 전부 쪽 없음)."""
    from zzaimy.app.pipeline import DocumentProcessor

    proc = DocumentProcessor.__new__(DocumentProcessor)
    proc._masker = None
    proc._last_pages = [(1, "첫 쪽의 본문입니다. " * 5), (7, "일곱째 쪽의 본문입니다. " * 5)]
    chunks = proc._page_chunks(do_mask=False)
    assert [c["page_no"] for c in chunks] == [1, 7]
    proc._last_pages = None
    assert proc._page_chunks(do_mask=False) is None


def test_vision_markdown_becomes_plain_text_without_html_tags():
    """비전 판독 결과의 <table> 은 본문에서 행 평문이 된다 — 태그가 기준 조각·검색에 들어가지 않는다."""
    from zzaimy.app.pipeline import DocumentProcessor

    md = ("## 2026년 2학기 기부장학금 선발 요약표\n\n(단위: 명)\n\n<table>\n<tr><th rowspan=\"2\">유형</th><th colspan=\"2\">심사기준</th></tr>\n"
          "<tr><th>1차</th><th>2차</th></tr>\n<tr><td>생활비</td><td>가계소득</td><td>자기소개서</td></tr>\n</table>\n\n문의: 장학팀")
    text = DocumentProcessor._md_to_text("```html\n[[속성]] 손글씨:아니오, 도장:아니오, 표:예\n" + md + "\n```")
    assert "<" not in text and "##" not in text and "속성" not in text and "```" not in text
    assert text.startswith("2026년 2학기 기부장학금 선발 요약표")
    assert "생활비 | 가계소득 | 자기소개서" in text and "문의: 장학팀" in text


def test_image_documents_go_to_vision_first_and_keep_table_chunks(tmp_path, monkeypatch):
    """사진·게시물은 갈래와 무관하게 판독 모델이 먼저 읽고, 표는 셀 구조 조각으로 남는다."""
    from zzaimy.app import pipeline as pl
    from zzaimy.app.pipeline import DocumentProcessor

    img = tmp_path / "poster.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
    monkeypatch.setattr(pl, "_vision_available", lambda: True)
    monkeypatch.setattr(pl, "_vision_model_name", lambda: "zzaimy-writer")
    monkeypatch.setattr(pl, "_format_mismatch", lambda p: "")
    proc = DocumentProcessor.__new__(DocumentProcessor)
    proc._masker = None
    monkeypatch.setattr(proc, "_crop_document_region", lambda p: None)
    monkeypatch.setattr(proc, "_vlm_transcribe", lambda p: "## 워크숍 맞춤형 김천관광체험\n\n| 기간 | 장소 |\n|---|---|\n| 연중 | 김천시 |")
    text = proc._parse_inner(img)
    assert text.startswith("워크숍 맞춤형 김천관광체험") and "연중 | 김천시" in text
    assert "비전 판독" in proc._last_parse_note
    chunks = proc._md_chunks(do_mask=False)
    assert [c["kind"] for c in chunks] == ["heading", "table"] and chunks[1]["page_no"] == 1
