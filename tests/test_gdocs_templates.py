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
