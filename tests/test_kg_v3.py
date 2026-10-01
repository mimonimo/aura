"""온톨로지 v3 — 사업 분류(문서가 알려 주는 다른 이름으로 묶기·근거·검토 대기), 절 트리, 근거 없는 관계 금지(ADR-0048)."""

import pytest

from zzaimy.graph import kg_store, programs, sections


def test_program_aliases_come_from_documents_and_classify_with_evidence():
    docs = [
        {"id": 1, "filename": "1.1차년도 LINC3.0 수정사업계획서.hwp", "head": "3단계 산학연협력 선도전문대학 육성사업(LINC 3.0) 1차년도 사업계획"},
        {"id": 2, "filename": "3. 3단계 산학연협력 선도전문대학 육성사업_평가 결과.hwpx", "head": "평가 결과 통보"},
        {"id": 3, "filename": "2026학년도 AID 전환 중점 전문대학 지원사업 공고문.pdf", "head": "AID(AI+Digital) 전환 중점 전문대학 지원사업 공고"},
        {"id": 4, "filename": "개설과목 목록.xlsx", "head": "과목명 | 학점", "path": "AID 전환 중점 전문대학 지원사업"},
        {"id": 5, "filename": "회의록.hwp", "head": "TRACK7 운영 회의"},
    ]
    cards = programs.build_cards(docs)
    linc = next(c for c in cards if programs._acr("LINC3.0") in {programs._acr(a) for a in c.acrs})
    assert any("산학연협력" in n for n in linc.names)                       # 약칭과 긴 이름이 한 카드
    got = {a.doc_id: a for a in programs.classify(docs, cards)}
    assert got[1].program == got[2].program == linc.node_id and got[1].round == 1 and got[1].kind == "plan"
    assert got[2].kind == "evaluation"
    assert got[3].program == got[4].program != linc.node_id                # 프로젝트 이름(경로)도 근거
    assert got[5].program == "" and got[5].status == "review"               # 'TRACK7' 은 사업이 아니다
    assert got[1].evidence and got[1].status == "auto"


def test_section_tree_paths_and_title_keys():
    chunks = [{"seq": 1, "kind": "heading", "content": "Ⅰ. 사업 개요"}, {"seq": 2, "kind": "text", "content": "개요 글"},
              {"seq": 3, "kind": "heading", "content": "1. 추진 배경"}, {"seq": 4, "kind": "text", "content": "배경 글"},
              {"seq": 5, "kind": "heading", "content": "1.1. 지역 여건"}, {"seq": 6, "kind": "table", "content": "표"},
              {"seq": 7, "kind": "heading", "content": "2. 추진 계획"}, {"seq": 8, "kind": "heading", "content": "Ⅱ. 성과관리"}]
    secs = sections.build(chunks)
    assert [(s.path, s.title) for s in secs] == [("1", "Ⅰ. 사업 개요"), ("1.1", "1. 추진 배경"), ("1.1.1", "1.1. 지역 여건"),
                                                 ("1.2", "2. 추진 계획"), ("2", "Ⅱ. 성과관리")]
    assert secs[0].chunks == [2] and secs[2].chunks == [6]
    assert sections.title_key("2-1. 산학협력단 조직") == sections.title_key("2.1 산학협력단 조직") == "산학협력단조직"


def test_edges_need_basis_and_evidence(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    with db._conn() as conn:
        kg_store.put_node(conn, "program:x", "program", "사업")
        with pytest.raises(ValueError):
            kg_store.put_edge(conn, "program:x", "doc:1", "contains", "분류", [])
        with pytest.raises(ValueError):
            kg_store.put_edge(conn, "program:x", "doc:1", "contains", "느낌", ["근거"])
        kg_store.put_edge(conn, "program:x", "doc:1", "contains", "분류", ["제목에 사업명"])
    assert kg_store.edges(db)[0]["evidence"] == ["제목에 사업명"]
