"""절 작성 에이전트의 재료 — 지시에서 대상 절 고르기, 절의 현재 글, 재료 묶기."""

from zzaimy.app import drafting

INFO = {"title": "작성서식 작업본", "text": "(앞머리)\n1. 대학의 여건\n1.1. 교육여건 분석\n【작성방법】 지역 동향과 인력수요를 쓴다 | 표\n1.2. 특성화 방향\n【작성방법】 비전을 쓴다\n이미 쓴 글\n2. 목표\n2.1. 추진목표\n【작성방법】 목표를 쓴다",
        "sections": [
            {"index": 0, "level": 0, "heading": "(앞머리)", "start": 1, "end": 5, "chars": 5, "table_end": 0},
            {"index": 1, "level": 2, "heading": "1. 대학의 여건", "start": 5, "end": 20, "chars": 0, "table_end": 0},
            {"index": 2, "level": 3, "heading": "1.1. 교육여건 분석", "start": 20, "end": 30, "chars": 0, "table_end": 60},
            {"index": 3, "level": 3, "heading": "1.2. 특성화 방향", "start": 60, "end": 90, "chars": 40, "table_end": 0},
            {"index": 4, "level": 2, "heading": "2. 목표", "start": 90, "end": 95, "chars": 0, "table_end": 0},
            {"index": 5, "level": 3, "heading": "2.1. 추진목표", "start": 95, "end": 100, "chars": 0, "table_end": 120},
        ]}


def test_target_sections_from_command():
    assert drafting.looks_like_section_draft("1.1 절을 작성방법에 맞춰 작성해 줘")
    assert drafting.looks_like_section_draft("다음 절 채워 줘") and drafting.looks_like_section_draft("전체 절 작성해")
    assert not drafting.looks_like_section_draft("1.1 절이 뭐야?") and not drafting.looks_like_section_draft("검토해 줘")
    assert [s["heading"] for s in drafting.target_sections(INFO, "1.1 절 작성해 줘")] == ["1.1. 교육여건 분석"]
    assert [s["heading"] for s in drafting.target_sections(INFO, "1.2 절을 다시 써 줘")] == ["1.2. 특성화 방향"]     # 번호를 말하면 찬 절도
    assert [s["heading"] for s in drafting.target_sections(INFO, "다음 절 작성해 줘")] == ["1.1. 교육여건 분석"]       # 첫 빈 절
    assert [s["heading"] for s in drafting.target_sections(INFO, "전체 채워 줘")] == ["1.1. 교육여건 분석", "2.1. 추진목표"]
    assert drafting.target_sections(INFO, "그냥 검토") == []


def test_section_text_and_materials_render():
    sec = INFO["sections"][2]
    txt = drafting.section_text(INFO, sec)
    assert txt.startswith("1.1. 교육여건 분석") and "지역 동향" in txt and "1.2." not in txt
    assert drafting.instruction_keywords(txt)[:2] == ["교육여건", "분석"]
    out = drafting.render_materials({"instructions": txt, "criteria": [{"reg_title": "평가편람", "content": "1.1 배점 10"}],
                                     "past": [{"title": "지난 계획서", "how": "같은 절", "text": "작년 여건"}]},
                                    {"대학명": "영남이공대학교", "총장": ""})
    assert "[이 절의 양식 안내·작성방법]" in out and "평가편람" in out and "《지난 계획서》 (같은 절)" in out
    assert "대학명: 영남이공대학교" in out and "총장" not in out              # 모르는 값은 asks 로 묻는다(재료에 적지 않음)


def test_materials_prefer_aligned_section_then_keywords():
    class DB:
        def list_documents(self, sector, project_id=None):
            return [{"id": 9, "filename": "지난 계획서.pdf", "status": "reviewed", "kind": "plan"},
                    {"id": 3, "filename": "서식.hwpx", "status": "reviewed", "kind": "form"},
                    {"id": 8, "filename": "현황표.xlsx", "status": "reviewed", "kind": "table"}]

        def list_doc_chunks(self, did):
            if did == 9:
                return [{"page_no": 1, "seq": 1, "kind": "text", "content": "1.1. 교육여건 분석\n지역 산업 수요가 는다"},
                        {"page_no": 2, "seq": 2, "kind": "text", "content": "1.2. 특성화 방향\n비전"}]
            return [{"page_no": 1, "seq": 1, "kind": "text", "content": "교육여건 분석 관련 현황 인력수요 표"}]

    nouns = lambda t: frozenset(w for w in t.replace("\n", " ").split() if len(w) >= 2)
    mats = drafting.Materials(DB(), {"id": 1, "sector": "grant"}, {3}, lambda *a, **k: [], nouns, [])
    m = mats.for_section(INFO, INFO["sections"][2], "1.1 절 작성해 줘", lambda f: f.rsplit(".", 1)[0])
    titles = {p["title"]: p for p in m["past"]}
    assert "지난 계획서" in titles and titles["지난 계획서"]["how"] == "같은 절" and "지역 산업 수요" in titles["지난 계획서"]["text"]
    assert "서식" not in titles                                     # 작업본의 원본 서식은 재료가 아니다
    assert titles["현황표"]["how"] == "낱말 겹침"


def test_unfilled_uses_body_chars_when_present():
    assert drafting.is_unfilled({"body_chars": 0, "chars": 300, "table_end": 0, "end": 10})       # 상자 글자만
    assert not drafting.is_unfilled({"body_chars": 120, "chars": 420, "table_end": 99, "end": 90})  # 모델이 넣은 표로 끝나도 본문 있음


def test_family_text_includes_form_subheadings():
    info = {"text": "", "sections": [
        {"index": 5, "level": 3, "heading": "1.1. 교육여건 분석", "start": 10, "end": 20, "chars": 0, "table_end": 30, "text": "1.1. 교육여건 분석\n【작성방법】 지역 동향"},
        {"index": 6, "level": 2, "heading": "1. 대외여건 분석", "start": 30, "end": 35, "chars": 0, "table_end": 0, "text": "1. 대외여건 분석"},
        {"index": 7, "level": 2, "heading": "1) 지역 동향", "start": 35, "end": 40, "chars": 0, "table_end": 0, "text": "1) 지역 동향"},
        {"index": 8, "level": 3, "heading": "1.2. 특성화 방향", "start": 40, "end": 50, "chars": 0, "table_end": 0, "text": "1.2. 특성화 방향"}]}
    subs = drafting.subsections(info, info["sections"][0])
    assert [s["index"] for s in subs] == [6, 7]
    fam = drafting.family_text(info, info["sections"][0])
    assert "[절 6] 1. 대외여건 분석" in fam and "[절 7] 1) 지역 동향" in fam and "1.2." not in fam


def test_score_against_reference_counts_numbers_and_terms():
    ref = "AI-X추진단을 신설하고 82개 교과목을 편성했다. NCSI 13년 연속 1위. VISION2030 수립. 사업비 240억원."
    draft = "총장 직속 AI-X추진단을 두고 82개 교과목을 편성하였다. VISION2030 과 연계한다."
    sc = drafting.score_against_reference(draft, ref)
    assert sc["total"] >= 5 and sc["covered"] >= 3 and 0 < sc["ratio"] < 1
    assert any("240억" in x for x in sc["missing"]) and not any("82개" in x for x in sc["missing"])
    assert drafting.score_against_reference("x", "")["ratio"] is None


def test_render_materials_shows_given_values():
    out = drafting.render_materials({"instructions": "x", "criteria": [], "past": []}, {"대학명": "영남이공대학교", "사업단명": "AI-X 사업단", "총장": ""})
    assert "[담당자가 알려 준 값·기관 정보]" in out and "사업단명: AI-X 사업단" in out and "총장" not in out


def test_score_ignores_bare_small_numbers():
    sc = drafting.score_against_reference("본문", "표 17 18 19 000 2026년 240억원 16건")
    assert not any(x in sc["missing"] for x in ("17", "18", "000")) and "240억원" in sc["missing"] and "16건" in sc["missing"]
