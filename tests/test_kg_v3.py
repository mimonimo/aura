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


def test_transposed_acronym_typo_joins_frequent_spelling():
    from zzaimy.graph import programs

    docs = [{"id": i, "filename": f"대구 RISE사업 자료 {i}.hwp", "path": "", "head": ""} for i in range(12)]
    docs.append({"id": 99, "filename": "2025년 RSIE사업 수정계획서.hwp", "path": "", "head": ""})
    cards = programs.build_cards(docs)
    rise = [c for c in cards if "RISE" in {a.upper() for a in c.acrs}]
    assert len(rise) == 1 and "RSIE" in {a.upper() for a in rise[0].acrs}


def test_listed_program_names_stay_separate_and_expansions_are_not_acronyms():
    from zzaimy.graph import programs

    names, acrs, pairs = programs._mentions("첨단분야 혁신융합대학(COSS)사업 · 전문대학 혁신지원(HiVE)사업")
    assert len(names) == 2 and {p[1] for p in pairs} == {"COSS", "HiVE"}
    assert all(("COSS" in n) != ("HiVE" in n) for n, _a in pairs)
    _n, acrs2, _p = programs._mentions("AID(AI+Digital) 전환 중점 전문대학 지원사업")
    assert "AI+Digital" not in acrs2


def test_file_name_outweighs_folder_for_program():
    from zzaimy.graph import programs

    docs = [{"id": i, "filename": f"LINC3.0 실적 {i}.hwp", "path": "링크/LINC3.0 업무", "head": ""} for i in range(3)]
    docs += [{"id": 9, "filename": "2023년 전문대학 혁신지원사업 최종사업계획서.hwp", "path": "링크/LINC3.0 업무", "head": ""},
             {"id": 10, "filename": "붙임1.hwp", "path": "링크/LINC3.0 업무", "head": ""}]
    cards = programs.build_cards(docs)
    res = {a.doc_id: a for a in programs.classify(docs, cards)}
    assert res[9].status == "review" and "혁신지원" in res[9].program_name
    assert res[10].status == "auto" and res[10].program == res[0].program


def test_acronym_matches_only_on_latin_word_boundary():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "대구 TECH사업 안내.hwp", "path": "", "head": ""},
            {"id": 2, "filename": "2024 Digitech Global Field Trip 결과보고서.pdf", "path": "", "head": ""}]
    res = {a.doc_id: a for a in programs.classify(docs, programs.build_cards(docs))}
    assert res[1].program and not res[2].program


def test_role_conn_env_override_only_for_that_process(tmp_path, monkeypatch):
    import json as _json
    from zzaimy.generate import llm_connections as lc

    cfg = tmp_path / "c.json"
    cfg.write_text(_json.dumps({"connections": [
        {"id": "a1", "name": "A", "kind": "vllm", "base_url": "http://a/v1", "model": "", "api_key": ""},
        {"id": "b2", "name": "B", "kind": "vllm", "base_url": "http://b/v1", "model": "", "api_key": ""}],
        "active": "a1", "roles": {"review": {"id": "b2", "model": "w"}}}), encoding="utf-8")
    lc.configure(cfg)
    assert lc.role_conn("review")["base_url"] == "http://b/v1"
    monkeypatch.setenv("ZZAIMY_ROLE_CONN", "review=a1")
    assert lc.role_conn("review")["base_url"] == "http://a/v1"
    monkeypatch.setenv("ZZAIMY_ROLE_CONN", "review=없음")
    assert lc.role_conn("review")["base_url"] == "http://b/v1"


def test_sections_promote_banner_tables_and_outline_lines():
    import json as _json
    from zzaimy.graph import sections

    banner = _json.dumps({"n_rows": 2, "n_cols": 2, "cells": [[0, 0, 2, 1, 0, "Ⅰ"], [0, 1, 1, 1, 0, "사업비전 및 목표"], [1, 1, 1, 1, 0, ""]]})
    chunks = [{"seq": 0, "kind": "heading", "content": "영남이공대학교"},
              {"seq": 1, "kind": "table", "content": banner},
              {"seq": 2, "kind": "text", "content": "1. 추진의 필요성 및 정책목표와의 연계성"},
              {"seq": 3, "kind": "heading", "content": "□ 사업목표"},
              {"seq": 4, "kind": "text", "content": "지역 산업 수요에 맞춘 인력을 기른다."},
              {"seq": 5, "kind": "text", "content": "1. 2017년 6월 사업단을 설치하였다."},
              {"seq": 6, "kind": "heading", "content": "15-2. 가족회사 운영 및 활성화"}]
    tree = sections.build(chunks)
    titles = {s.title: s for s in tree}
    assert titles["Ⅰ. 사업비전 및 목표"].level == 1
    assert titles["1. 추진의 필요성 및 정책목표와의 연계성"].parent == titles["Ⅰ. 사업비전 및 목표"].path
    assert "□ 사업목표" not in titles and 3 in titles["1. 추진의 필요성 및 정책목표와의 연계성"].chunks
    assert "1. 2017년 6월 사업단을 설치하였다." not in titles
    assert titles["15-2. 가족회사 운영 및 활성화"].level == 3


def test_sections_skip_toc_runs_and_follow_page_order():
    from zzaimy.graph import sections

    toc = [{"seq": 10 + i, "kind": "text", "content": f"{i + 1}. 목차 항목 {i + 1}", "page_no": 2} for i in range(6)]
    chunks = [{"seq": 1, "kind": "text", "content": "Ⅰ. 비전 및 체제", "page_no": 5},      # 간지 제목이 앞 seq 로
              {"seq": 2, "kind": "text", "content": "Ⅱ. 인력양성", "page_no": 9}] + toc + [
              {"seq": 30, "kind": "heading", "content": "1. 사업 비전 및 목표", "page_no": 5},
              {"seq": 31, "kind": "text", "content": "비전은 지역과 함께 성장하는 것이다.", "page_no": 6},
              {"seq": 32, "kind": "heading", "content": "2. 교육과정", "page_no": 9},
              {"seq": 33, "kind": "text", "content": "교육과정을 개편하였다.", "page_no": 10}]
    tree = sections.build(chunks)
    titles = {s.title: s for s in tree}
    assert not any(t.startswith("1. 목차") for t in titles)
    assert titles["1. 사업 비전 및 목표"].parent == titles["Ⅰ. 비전 및 체제"].path
    assert titles["2. 교육과정"].parent == titles["Ⅱ. 인력양성"].path


def test_section_ids_unique_and_round_bullets_are_body():
    from zzaimy.graph import sections

    chunks = [{"seq": 0, "kind": "heading", "content": "1. 결과 통보 공문"},
              {"seq": 1, "kind": "heading", "content": "Ⅰ. 사업 개요"},
              {"seq": 2, "kind": "heading", "content": "❍ 별도 계좌 개설"},
              {"seq": 3, "kind": "heading", "content": "1. 일반현황"}]
    tree = sections.build(chunks)
    ids = [s.path for s in tree]
    assert len(ids) == len(set(ids))
    assert not any(s.title.startswith("❍") for s in tree)


def test_unnumbered_title_heads_a_restarted_list():
    from zzaimy.graph import sections

    chunks = [{"seq": 0, "kind": "heading", "content": "Ⅵ. 우수사례"}]
    for k, case in enumerate(("하이드로젤 공동연구", "로봇 현장실습")):
        base = 10 * (k + 1)
        chunks += [{"seq": base, "kind": "heading", "content": case},
                   {"seq": base + 1, "kind": "heading", "content": "1. 추진배경 및 개요"},
                   {"seq": base + 2, "kind": "text", "content": "배경 설명 문단이다."},
                   {"seq": base + 3, "kind": "heading", "content": "2. 추진과정"},
                   {"seq": base + 4, "kind": "text", "content": "과정 설명 문단이다."}]
    tree = sections.build(chunks)
    by = {s.path: s for s in tree}
    firsts = [s for s in tree if s.title == "1. 추진배경 및 개요"]
    assert {by[s.parent].title for s in firsts} == {"하이드로젤 공동연구", "로봇 현장실습"}


def test_sections_from_pdf_page_text_lines():
    from zzaimy.graph import sections

    head = "Ⅱ. 사업 추진내용\n 17\n"
    pages = [
        {"seq": 1, "kind": "text", "page_no": 2, "content": "목 차\n1. 사업 추진체계 ······ 17\n2. 교육과정 ······ 20\n3. 성과관리 ······ 30\n" + "x" * 200},
        {"seq": 2, "kind": "text", "page_no": 17, "content": head + "1. 사업 추진체계\n1-1. 사업 목표 및 추진체계\n정부는 AI 전환을 핵심 동력으로 설정하고 있음. " + "가" * 200},
        {"seq": 3, "kind": "text", "page_no": 18, "content": head + "본문이 이어진다. " + "나" * 200},
        {"seq": 4, "kind": "text", "page_no": 19, "content": head + "2. 교육과정\n교육과정을 운영함. " + "다" * 200}]
    tree = sections.build(pages)
    titles = {s.title: s for s in tree}
    assert "Ⅱ. 사업 추진내용" not in titles and "1. 사업 추진체계" in titles and "2. 교육과정" in titles
    assert titles["1-1. 사업 목표 및 추진체계"].parent == titles["1. 사업 추진체계"].path
    assert 3 in titles["1-1. 사업 목표 및 추진체계"].chunks
    assert not any("······" in t for t in titles)


def test_wrapped_pdf_header_within_1024_bytes_is_pdf(tmp_path):
    from zzaimy.app.pipeline import _format_mismatch

    f = tmp_path / "공문.pdf"
    f.write_bytes(b"Handysoft Approval Document File" + b"\x00" * 397 + b"%PDF-1.6\n%%EOF\n")
    assert _format_mismatch(f) is None
    g = tmp_path / "가짜.pdf"
    g.write_bytes(b"\x00" * 2000)
    assert _format_mismatch(g)


def test_banner_with_ascii_roman_or_decorated_second_cell():
    import json as _json
    from zzaimy.graph import sections

    b3 = _json.dumps({"n_rows": 2, "n_cols": 3, "cells": [[0, 0, 2, 1, 0, "III. 기업가치 창출"], [1, 2, 1, 1, 0, "Y-verse Platform 3.0"]]})
    b5 = _json.dumps({"n_rows": 2, "n_cols": 3, "cells": [[0, 0, 2, 1, 0, "Ⅴ. 지속가능성"], [0, 1, 1, 2, 0, "미래가치 1등 직업교육대학 " * 6]]})
    assert sections.heading_text({"kind": "table", "content": b3}).startswith("III. 기업가치 창출")
    assert sections.heading_text({"kind": "table", "content": b5}) == "Ⅴ. 지속가능성"


def test_align_instances_need_body_and_children_follow_parents():
    from zzaimy.graph import sections

    def doc(case_body):
        chunks = [{"seq": 0, "kind": "heading", "content": "Ⅵ. 우수사례"},
                  {"seq": 1, "kind": "heading", "content": "[인력양성] 우수사례 1"},
                  {"seq": 2, "kind": "text", "content": case_body},
                  {"seq": 3, "kind": "heading", "content": "1. 추진배경 및 개요"},
                  {"seq": 4, "kind": "text", "content": case_body + " 배경"},
                  {"seq": 5, "kind": "heading", "content": "9. 차년도 사업계획"},
                  {"seq": 6, "kind": "heading", "content": "9-1. 특화분야 산학협력 브랜드 창출"},
                  {"seq": 7, "kind": "text", "content": "브랜드 창출 계획 특화분야 협력 확산"}]
        return sections.build(chunks)
    body_a = "하이드로젤 소재 공동연구 기능성 의료 소재 개발"
    body_b = "배달 로봇 부품 시제품 소스 개발 현장실습"
    a, b = doc(body_a), doc(body_b)
    text = {id(x): (body_a if x.chunks and x.title != "9-1. 특화분야 산학협력 브랜드 창출" else "") for x in a}
    text.update({id(y): (body_b if y.chunks and y.title != "9-1. 특화분야 산학협력 브랜드 창출" else "") for y in b})
    import re as _re
    pairs = sections.align_context(a, b, text_of=lambda s: text.get(id(s), ""), skip_b=_re.compile(r"차년도"))
    titles = {(x.title, y.title) for x, y, _w in pairs}
    assert ("[인력양성] 우수사례 1", "[인력양성] 우수사례 1") not in titles
    assert ("1. 추진배경 및 개요", "1. 추진배경 및 개요") not in titles
    assert not any(y.title.startswith("9-1.") for _x, y, _w in pairs)


def test_renumbered_case_matched_by_content():
    from zzaimy.graph import sections

    def doc(cases):
        chunks, seq = [{"seq": 0, "kind": "heading", "content": "Ⅵ. 우수사례"}], 1
        for label, body in cases:
            chunks += [{"seq": seq, "kind": "heading", "content": label},
                       {"seq": seq + 1, "kind": "heading", "content": "1. 추진배경 및 개요"},
                       {"seq": seq + 2, "kind": "text", "content": body}]
            seq += 3
        return sections.build(chunks), {c["seq"]: c["content"] for c in chunks}
    a, ta = doc([("[공유·협업] 우수사례 1", "가족회사 등급제 운영 유료 가족회사 확대 산업체 협력")])
    b, tb = doc([("[공유·협업] 우수사례 1", "해외 박람회 벤치마킹 디지텍 협의회 일본 견학"),
                 ("[공유·협업] 우수사례 2", "가족회사 등급제 운영 유료 가족회사 확대 산업체 협력")])
    text = {id(x): " ".join(ta.get(q, "") for q in x.chunks) for x in a}
    text.update({id(y): " ".join(tb.get(q, "") for q in y.chunks) for y in b})
    pairs = sections.align_context(a, b, text_of=lambda s: text.get(id(s), ""))
    case = [(x.title, y.title) for x, y, _w in pairs if x.title.startswith("[")]
    assert case == [("[공유·협업] 우수사례 1", "[공유·협업] 우수사례 2")]
    kids = [(x, y) for x, y, _w in pairs if x.title.startswith("1. 추진배경")]
    assert len(kids) == 1 and {s.path: s for s in b}[kids[0][1].parent].title == "[공유·협업] 우수사례 2"


def test_body_phrase_alone_is_not_a_program():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "2025 자체평가 보고서.hwp", "path": "", "head": "각종 결재 시 반드시 산학협력단 해당사업 확인"},
            {"id": 2, "filename": "대구 RISE사업 운영 지침.hwp", "path": "", "head": ""}]
    names = {c.name for c in programs.build_cards(docs)}
    assert not any("각종" in n for n in names) and any("RISE" in n for n in names)


def test_short_name_merges_into_single_long_name():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "2025 혁신지원사업 자율성과지표.hwp", "path": "", "head": ""},
            {"id": 2, "filename": "2023년 전문대학 혁신지원사업 최종사업계획서.hwp", "path": "", "head": ""}]
    cards = programs.build_cards(docs)
    assert len([c for c in cards if any("혁신지원" in n for n in c.names)]) == 1


def test_units_group_recurring_titles_across_documents():
    from zzaimy.graph import sections, units

    def doc(items):
        chunks = []
        for i, t in enumerate(items):
            chunks += [{"seq": 2 * i, "kind": "heading", "content": t}, {"seq": 2 * i + 1, "kind": "text", "content": "본문 문단이다."}]
        return sections.build(chunks)
    d1 = doc(["Ⅲ. 기업가치 창출", "15-2. 가족회사 운영 및 활성화 실적의 적정성", "1. 추진배경 및 개요", "1. 추진배경 및 개요", "1. 추진배경 및 개요"])
    d2 = doc(["Ⅲ. 기업가치 창출", "18-2. 가족회사 운영 및 활성화 실적의 적정성", "[인력양성] 우수사례 1"])
    d3 = doc(["Ⅰ. 다른 장 제목만 있는 문서"])
    got = {u.label: u for u in units.build([(1, d1), (2, d2), (3, d3)])}
    assert "가족회사 운영 및 활성화 실적의 적정성" in got and got["가족회사 운영 및 활성화 실적의 적정성"].docs == {1, 2}
    assert "기업가치 창출" in got
    assert not any("추진배경" in k or "우수사례" in k or "다른 장" in k for k in got)


def test_title_key_keeps_first_syllable_and_code_units():
    from zzaimy.graph import sections, units

    assert sections.title_key("영남이공대학교") == "영남이공대학교"
    assert sections.title_key("가. 사업 개요") == "사업개요"
    assert sections.title_key("III. 기업가치 창출") == "기업가치창출"
    assert units.doc_code("02-4_RISE_2차년도_과제계획서_2-3_영남이공대학교.hwp") == "2-3"
    assert units.doc_code("RISE_단위과제(1-1) 과제계획서(주관).hwpx") == "1-1"

    def doc(items):
        chunks = []
        for i, t in enumerate(items):
            chunks += [{"seq": 2 * i, "kind": "heading", "content": t}, {"seq": 2 * i + 1, "kind": "text", "content": "본문이다."}]
        return sections.build(chunks)
    items = [(1, doc(["1. 과제 배경 및 목표"])), (2, doc(["1. 과제 배경 및 목표"])), (3, doc(["1. 과제 배경 및 목표"]))]
    got = {u.key: u for u in units.build(items, codes={1: "2-3", 2: "2-3", 3: "1-1"})}
    assert "#2-3" in got and got["#2-3"].docs == {1, 2}
    assert any(k.startswith("#2-3/") for k in got) and not any(k.startswith("#1-1/") for k in got)
    assert "과제배경및목표" not in got


def test_renamed_program_merges_with_document_evidence():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "대구 RISE사업 운영 지침.hwp", "path": "", "head": ""},
            {"id": 2, "filename": "2026년 지역성장 인재양성체계(앵커)사업 수정계획서 제출 안내.hwp", "path": "", "head": ""},
            {"id": 3, "filename": "[붙임] RISE(現 앵커) 사업비 집행 및 관리 지침 FAQ.hwpx", "path": "", "head": ""},
            {"id": 4, "filename": "2026년 대구 앵커사업 사업비 2차 교부 제출서류.hwp", "path": "", "head": ""},
            {"id": 5, "filename": "산학공동 기술개발과제 협약(주관) 사업계획서.hwp", "path": "", "head": ""}]
    cards = programs.build_cards(docs)
    rise = [c for c in cards if "RISE" in c.acrs]
    assert len(rise) == 1 and "앵커" in rise[0].acrs and rise[0].renamed
    assert any("대구 앵커사업" in n for n in rise[0].names)
    assert not any("주관" in a for c in cards for a in c.acrs)
    res = {a.doc_id: a for a in programs.classify(docs, cards)}
    assert res[4].program == res[1].program == res[2].program


def test_rename_needs_explicit_marker_in_file_name():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "2024년_대학_산학협력활동_실태조사_지침서.hwp",
             "path": "링크/업무공유(LINC사업단)/2.사회맞춤형 산학협력선도전문대학(LINC+)육성사업 현황/3.3단계 산학연협력 선도전문대학 육성사업(LINC3.0)", "head": ""},
            {"id": 2, "filename": "사회맞춤형 산학협력 선도전문대학(LINC+) 육성사업 실적보고서.hwp", "path": "", "head": ""},
            {"id": 3, "filename": "3단계 산학연협력 선도전문대학 육성사업(LINC 3.0) 계획서.hwp", "path": "", "head": ""}]
    cards = programs.build_cards(docs)
    linc_plus = [c for c in cards if "LINC+" in c.acrs]
    assert linc_plus and not any(a.replace(" ", "") == "LINC3.0" for a in linc_plus[0].acrs)


def test_aspect_match_links_report_aspect_to_plan_chapter():
    from zzaimy.graph import sections

    chunks = []
    for i, t in enumerate(["Ⅰ. 과제 배경 및 목표", "Ⅲ. 세부과제 추진 내용", "Ⅳ. 성과지표 관리 계획", "Ⅴ. 예산 운용"]):
        chunks += [{"seq": 2 * i, "kind": "heading", "content": t}, {"seq": 2 * i + 1, "kind": "text", "content": "본문이다."}]
    tree = sections.build(chunks)
    assert sections.aspect_match(tree, "과제2-1 예산 집행 실적")[0].title == "Ⅴ. 예산 운용"
    assert sections.aspect_match(tree, "[과제2-1] 성과지표 달성 실적")[0].title == "Ⅳ. 성과지표 관리 계획"
    assert sections.aspect_match(tree, "[과제 2-1] 우수사례")[0] is None


def test_restarted_numbering_in_page_text_is_not_headings():
    from zzaimy.graph import sections

    page = {"seq": 1, "kind": "text", "page_no": 5, "content": "Ⅱ. 인력양성\n1. 산학연계 직업기초교육 프로\n2. 산업체가 요구하는 문제해결\n"
            "3. 산학협력을 통해 지속적\n1. 산학협업 혁신적 교육\n2. 혁신적 교육방법 협업\n" + "본문 " * 80}
    tree = sections.build([page])
    assert not any(s.title.startswith("1. 산학") for s in tree)


def test_leading_time_and_ordinal_tags_dropped_from_program_names():
    from zzaimy.graph import programs

    names = [programs._mentions(t)[0] for t in ("8월 RISE사업 실적", "3(경대) RISE사업 협약", "25재정지원사업 정산")]
    flat = [n for ns in names for n in ns]
    assert not any(n.startswith(("8월", "3(", "25")) for n in flat)


def test_apply_reviews_only_touches_unconfirmed_files():
    from zzaimy.graph import programs

    docs = [{"id": 1, "filename": "LINC3.0 계획서.hwp", "path": "링크/LINC3.0", "head": ""},
            {"id": 2, "filename": "붙임1.hwp", "path": "산단/이전자료/회계/서식", "head": ""},
            {"id": 3, "filename": "붙임2.hwp", "path": "앵커/02_2차년도/03_프로그램", "head": ""}]
    cards = programs.build_cards(docs)
    res = programs.classify(docs, cards)
    reviews = [{"folder": "산단/이전자료/회계", "label": "사업 아님", "confidence": "high", "reason": "회계 서식"},
               {"folder": "앵커/02_2차년도", "label": "새 사업: 지역성장 인재양성체계", "confidence": "medium", "reason": "앵커"},
               {"folder": "링크", "label": "사업 아님", "confidence": "high", "reason": "틀린 판정"}]
    n = programs.apply_reviews(docs, res, reviews, cards)
    by = {a.doc_id: a for a in res}
    assert by[1].status == "auto" and by[1].program            # 규칙이 확정한 것은 그대로
    assert by[2].status == "agent" and by[2].program == ""
    assert by[3].status == "agent" and "지역성장" in by[3].program_name
    assert n == 2


def test_stage_number_kept_in_program_name():
    from zzaimy.graph import programs

    names = programs._mentions("(영남이공대학교)3단계 산학연협력 선도전문대학 육성사업 계획서")[0]
    assert any(n.startswith("3단계") for n in names)
