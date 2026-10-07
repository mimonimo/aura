"""구글 독스 공통 양식 4종 사양 — 독스 규칙(표 8열 이하·모양 일치·제목 단계)과 지침·실문서 값 없음."""
from zzaimy.ingest import gdocs_templates as gt


def test_specs_follow_docs_rules():
    assert set(gt.SPECS) == {"plan", "report", "program_plan", "program_report"}
    for sid, spec in gt.SPECS.items():
        assert gt.check_spec(spec) == [], sid
        heads = [b["text"] for b in spec["blocks"] if "h" in b]
        guides = [b for b in spec["blocks"] if "guide" in b]
        tables = [b for b in spec["blocks"] if "table" in b]
        assert len(heads) >= 8 and len(guides) >= 5 and len(tables) >= 5, sid
        text = gt.outline_text(spec)
        assert "영남이공" not in text and "2023" not in text           # 실문서 값은 넣지 않는다


def test_plan_has_common_backbone_and_rules():
    heads = [b["text"] for b in gt.PLAN["blocks"] if "h" in b]
    for part in ("Ⅰ. 사업 추진 목표", "Ⅱ. 사업 추진 체계", "Ⅲ. 사업 추진 계획", "Ⅳ. 성과관리 계획", "Ⅴ. 재정투자 계획"):
        assert part in heads
    text = gt.outline_text(gt.PLAN)
    assert "계산하지 말고" in text and "그대로 옮기고" in text           # 절대 규칙 1·5 를 지침에


def test_check_spec_catches_wide_tables():
    bad = {"blocks": [gt.H(1, "가"), gt.T([str(i) for i in range(9)], 1)]}
    assert gt.check_spec(bad)


def test_fit_widths_keeps_header_words_on_one_line():
    heads = ["구분", "지표명(단위)", "기준값", "1차년도", "2차년도", "3차년도", "4차년도", "측정 방법·증빙"]
    w = gt.fit_widths(heads, [1, 2.4, 1, 1, 1, 1, 1, 2])
    assert abs(sum(w) - gt.CONTENT_W) < 1
    assert all(x >= gt._min_width(h) - 0.1 for x, h in zip(w, heads))   # 「1차년도」가 「1차년/도」로 꺾이지 않는다
    key, val = gt.fit_widths(["사업명", "총 사업비(국비·지방비·대응자금)"], [1, 3], kv=True)
    assert key >= gt._min_width("(국비·지방비·대응자금)") and key <= gt.CONTENT_W * 0.35 + 0.1


def test_check_spec_catches_section_without_guide():
    assert gt.check_spec({"blocks": [gt.H(1, "가"), gt.P()]}) == ["작성 지침·표가 없는 절: 가"]
    assert gt.check_spec({"blocks": [gt.H(1, "가"), gt.G("지침"), gt.P()]}) == []
