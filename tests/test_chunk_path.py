from zzaimy.app.chunk_path import attach_paths, level_of


def test_levels_from_numbering():
    assert level_of("Ⅰ. 사업추진 목표") == 1 and level_of("1. 대학의 여건") == 2 and level_of("1.1. 교육여건 분석") == 3
    assert level_of("2.1.1. 총괄표") == 4 and level_of("1) 강점(S)") == 5 and level_of("가. 세부") == 5
    assert level_of("【작성방법】") == 2 and level_of("국가 정책 동향") == 0


def test_attach_paths_builds_breadcrumbs_in_document_order():
    chunks = [
        {"page_no": 1, "seq": 1, "kind": "heading", "content": "Ⅰ. 사업추진 목표"},
        {"page_no": 1, "seq": 2, "kind": "heading", "content": "1. 대학의 여건 및 특성화 방향"},
        {"page_no": 1, "seq": 3, "kind": "heading", "content": "1.1. 교육여건 분석"},
        {"page_no": 1, "seq": 4, "kind": "heading", "content": "**국가 정책 동향**"},
        {"page_no": 1, "seq": 5, "kind": "text", "content": "AI 3대 강국 도약"},
        {"page_no": 2, "seq": 6, "kind": "heading", "content": "1.2. 특성화 방향"},
        {"page_no": 2, "seq": 7, "kind": "table", "content": "{}"},
        {"page_no": 3, "seq": 8, "kind": "heading", "content": "Ⅱ. 사업추진 실적 및 계획"},
        {"page_no": 3, "seq": 9, "kind": "text", "content": "본문"},
    ]
    out = attach_paths(chunks)
    assert out[4]["path_text"] == "Ⅰ. 사업추진 목표 > 1. 대학의 여건 및 특성화 방향 > 1.1. 교육여건 분석 > 국가 정책 동향"
    assert out[6]["path_text"] == "Ⅰ. 사업추진 목표 > 1. 대학의 여건 및 특성화 방향 > 1.2. 특성화 방향"     # 잎 제목은 1.2 에서 밀려난다
    assert out[8]["path_text"] == "Ⅱ. 사업추진 실적 및 계획"
    assert chunks[4].get("path") is None                                                    # 원본은 그대로
