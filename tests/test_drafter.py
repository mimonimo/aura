

def test_section_evidence_blends_materials_and_regulations(monkeypatch):
    """섹션 근거 = 접수 자료 조각 + 실검색(하이브리드·리랭커) 규정 조각."""
    from zzaimy.app import drafter as drafter_mod
    from zzaimy.retrieve.stub import Evidence

    fake_hits = [
        {"reg_title": "장학 지침", "heading": "제3조 지원대상",
         "content": "지원대상은 재학생으로 한다.", "doc_id": 9},
        {"reg_title": "장학 지침", "heading": "제5조 예산",
         "content": "예산은 250만 원 이내로 한다.", "doc_id": 9},
    ]
    monkeypatch.setattr(
        drafter_mod, "_find_relevant",
        lambda db, q, top_k=4: fake_hits[:top_k],
    )
    materials = [Evidence(text="신청서 본문", source_doc="신청서.pdf", source_page=1)]
    ev = drafter_mod.build_section_evidence(
        db=None, section_query="지원 대상", materials=materials,
    )
    texts = [e.text for e in ev]
    assert "신청서 본문" in texts[0]
    assert any("지원대상은 재학생" in t for t in texts)
    srcs = {e.source_doc for e in ev}
    assert any("장학 지침" in s for s in srcs)
