from zzaimy.app.delegate import parse_sub, strip_reasoning


def test_english_reasoning_lines_dropped_korean_kept():
    raw = "Need ensure no 양식 예시 글자 (○○·000). None.\n[본문]\n1. AI 교육 과정 운영(LINC 3.0 연계)\n| 구분 | AI |\n"
    body, _ = parse_sub(raw)
    assert "Need ensure" not in body
    assert "AI 교육 과정 운영(LINC 3.0 연계)" in body and "| 구분 | AI |" in body


def test_close_think_only():
    body, _ = parse_sub("thinking here...</think>[본문]\n가. 총 목표")
    assert body == "가. 총 목표"


def test_strip_keeps_short_terms():
    assert strip_reasoning("ICT 기반 AI 교육") == "ICT 기반 AI 교육"
