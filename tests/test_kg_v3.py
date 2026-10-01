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


def test_longer_program_name_masks_nested_shorter_one():
    docs = [{"id": 1, "filename": "AID 전환 중점 전문대학 지원사업 계획서.hwp", "head": "AID 전환 중점 전문대학 지원사업 계획"},
            {"id": 2, "filename": "전문대학 지원사업 안내.hwp", "head": "전문대학 지원사업 안내"}]
    cards = programs.build_cards(docs)
    got = {a.doc_id: a for a in programs.classify(docs, cards)}
    assert got[1].share == 1.0 and got[1].status == "auto"                    # 짧은 이름이 몫을 나눠 갖지 않는다
    assert got[2].program != got[1].program


def test_align_uses_parent_titles_for_repeated_titles():
    def secs(spec):
        out = []
        for path, title in spec:
            parent = path.rsplit(".", 1)[0] if "." in path else ""
            out.append(sections.Section(path=path, title=title, level=path.count(".") + 1, seq=0, parent=parent))
        return out
    plan = secs([("1", "1. 과제 가 운영"), ("1.1", "4. 기대효과 및 향후 과제"), ("2", "2. 과제 나 운영"), ("2.1", "4. 기대효과 및 향후 과제"),
                 ("3", "3. 예산 운영의 적절성")])
    report = secs([("1", "1. 과제 나 운영"), ("1.1", "4. 기대효과 및 향후 과제"), ("2", "2. 과제 다 운영"), ("2.1", "4. 기대효과 및 향후 과제"),
                   ("3", "9. 예산 운영의 적절성")])
    pairs = {(x.path, y.path) for x, y, _ in sections.align(plan, report)}
    assert ("2.1", "1.1") in pairs                       # 과제 나 아래 기대효과끼리
    assert ("1.1", "2.1") not in pairs and ("1.1", "1.1") not in pairs   # 다른 과제의 기대효과끼리는 잇지 않는다
    assert ("3", "3") in pairs and ("2", "1") in pairs   # 하나뿐인 제목은 번호가 달라도


def test_align_context_prefers_similar_parent_and_body():
    def S(path, title):
        return sections.Section(path=path, title=title, level=path.count(".") + 1, seq=0,
                                parent=path.rsplit(".", 1)[0] if "." in path else "")
    plan = [S("1", "1. 가족회사 운영 및 활성화 계획"), S("1.1", "4. 기대효과 및 향후 과제"),
            S("2", "2. 산학공동연구 기술개발 계획"), S("2.1", "4. 기대효과 및 향후 과제")]
    report = [S("1", "1. 산학공동연구 기술개발 실적"), S("1.1", "4. 기대효과 및 향후 과제"),
              S("2", "2. 가족회사 운영 및 활성화 실적"), S("2.1", "4. 기대효과 및 향후 과제")]
    body = {("1.1", "p"): "가족회사 협약 기업 수 확대", ("2.1", "p"): "공동연구 과제 특허",
            ("1.1", "r"): "공동연구 과제 특허 출원", ("2.1", "r"): "가족회사 협약 기업 확대"}
    side = {id(x): "p" for x in plan} | {id(x): "r" for x in report}
    pairs = {(x.path, y.path) for x, y, _ in sections.align_context(plan, report, lambda s: body.get((s.path, side[id(s)]), ""))}
    assert ("1.1", "2.1") in pairs and ("2.1", "1.1") in pairs                # 상위 제목이 조금 달라도(계획/실적) 맥락으로
    assert ("1.1", "1.1") not in pairs


def test_graph_retrieve_narrows_program_year_kind_and_traces_steps(tmp_path):
    from zzaimy.app.db import Database
    from zzaimy.graph import retrieve

    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    with db._conn() as c:
        kg_store.put_node(c, "program:linc30", "program", "3단계 산학연협력 선도전문대학 육성사업", {"names": ["3단계 산학연협력 선도전문대학 육성사업"], "acronyms": ["LINC3.0"]})
        kg_store.put_node(c, "program:aid", "program", "AID 전환 중점 전문대학 지원사업", {"names": ["AID 전환 중점 전문대학 지원사업"], "acronyms": []})
        for r in (1, 3):
            kg_store.put_node(c, f"year:linc30:r{r}", "year", f"LINC {r}차년도", {"round": r})
            kg_store.put_edge(c, "program:linc30", f"year:linc30:r{r}", "contains", "분류", ["분류"])
            for kind, did in (("plan", r * 10), ("report", r * 10 + 1)):
                kg_store.put_node(c, f"doc:{did}", "doc", f"{r}차년도 {kind}", {"kind": kind}, did)
                kg_store.put_edge(c, f"year:linc30:r{r}", f"doc:{did}", "contains", "분류", ["분류"])
                kg_store.put_node(c, f"doc:{did}:sec:1", "section", "가족회사 운영 및 활성화", {"chunks": []}, did)
                kg_store.put_edge(c, f"doc:{did}", f"doc:{did}:sec:1", "contains", "구조", ["목차"])
    tr = retrieve.retrieve(db, "LINC3.0 3차년도 실적보고서에서 가족회사 운영 실적은?")
    assert tr.program == "program:linc30" and tr.round == 3 and tr.kinds == ["report"]
    assert tr.hits and tr.hits[0].section == "doc:31:sec:1" and tr.hits[0].path[-1] == "가족회사 운영 및 활성화"
    assert tr.steps[0].startswith("[1단계: 질문 파악]") and any(s.startswith("[3단계") for s in tr.steps)


def test_graph_retrieve_leading_acronym_and_sectionless_doc_fallback(tmp_path):
    from zzaimy.app.db import Database
    from zzaimy.graph import retrieve

    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    with db._conn() as c:
        kg_store.put_node(c, "program:aid", "program", "AID 전환 중점 전문대학 지원사업",
                          {"names": ["AID (AI+Digital) 전환 중점 전문대학 지원사업"], "acronyms": []})
        kg_store.put_node(c, "doc:7", "doc", "평가 종합의견", {"kind": "evaluation"}, 7)
        kg_store.put_edge(c, "program:aid", "doc:7", "contains", "분류", ["분류"])
    body = {0: "총평 문단", 1: "현장실습 운영 지적 사항: 참여 기업 확대 필요"}

    def chunk_text(doc_id, seqs):
        return list(body.items()) if seqs is None else "\n".join(body[s] for s in seqs)
    tr = retrieve.retrieve(db, "AID 사업 평가에서 현장실습 지적 사항은?", chunk_text=chunk_text)
    assert tr.program == "program:aid"
    assert tr.hits and tr.hits[0].section == "doc:7:chunk:1" and tr.hits[0].path[-1] == "본문 2"


def test_program_names_drop_org_labels_years_and_join_inner_acronyms():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "(영남이공대학교)3단계 산학연협력 선도전문대학 육성사업 계획서.hwp", "path": "링크/25.1차년도(2025) 사업 진행", "head": ""},
            {"id": 2, "filename": "지역혁신중심 대학지원체계(RISE)사업 시행계획.hwp", "path": "", "head": ""},
            {"id": 3, "filename": "대구 RISE사업 운영 지침.hwp", "path": "", "head": ""}]
    cards = programs.build_cards(docs)
    surfaces = {s for c in cards for s in c.surfaces()}
    assert not any("영남이공대학교" == s or s == "2025" or "1차년도" in s for s in surfaces)
    rise = [c for c in cards if "RISE" in {a.upper() for a in c.acrs}]
    assert len(rise) == 1 and any("대구" in n for n in rise[0].names) and any("지역혁신중심" in n for n in rise[0].names)


def test_inherit_by_folder_respects_program_year_span():
    from zzaimy.graph import programs

    docs, res = [], []
    for i in range(12):
        docs.append({"id": i, "filename": f"3차년도 실적 {i}.hwp", "path": f"링크/LINC3.0 모음/{2022 + i % 3}년"})
        res.append(programs.Assignment(doc_id=i, program="program:linc30", program_name="LINC3.0", status="auto", year=2022 + i % 3))
    for i in range(12, 16):
        docs.append({"id": i, "filename": f"계획 {i}.hwp", "path": "링크/LINC+ 자료/2018년"})
        res.append(programs.Assignment(doc_id=i, program="program:linc", program_name="LINC+", status="auto", year=2018))
    docs += [{"id": 90, "filename": "증빙.pdf", "path": "링크/2차년도(2023)/정성"},
             {"id": 91, "filename": "증빙.pdf", "path": "링크/옛 자료(2019)/정성"}]
    res += [programs.Assignment(doc_id=90), programs.Assignment(doc_id=91)]
    assert programs.inherit_by_folder(docs, res) >= 1
    assert res[-2].program == "program:linc30" and res[-2].status == "folder"
    assert res[-1].program in ("", "program:linc")          # 2019 는 LINC3.0 기간(2022~2024) 밖
