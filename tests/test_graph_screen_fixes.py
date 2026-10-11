"""그래프 화면·프로젝트 검색 정리 — 문장 조각 별칭, 양식 자리표시 절, 각주 번호, 사업 이름·문서 수 정의, 쪽 나눔, 진행 중 사업.

사례는 모두 지어낸 이름이다. 규칙이 특정 사업·문서에 맞춘 것이 아니라 꼴로 판정하는지 본다."""
from __future__ import annotations

import datetime

from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import kg_explore, project_refs
from zzaimy.app.db import Database
from zzaimy.app.kg_view import CELL_MAX, match_degree, program_view, year_page
from zzaimy.graph import indicators, kg_store, programs, sections


# ── 별칭: 문장 조각 거르기 ──────────────────────────────────────────────────
def test_fragment_name_rejects_sentence_pieces_by_shape():
    fragments = [
        "그리고 2019년부터 추진된 미래인재 양성사업",        # 접속 부사로 시작, 「2019년부터」
        "나아가 해양바이오 거점 육성사업",                   # 접속 부사
        "지역 기업과 협력하여 추진하는 산학 공동 연구사업",   # 「협력하여」「추진하는」
        "센터에서 운영하던 창업 보육 지원사업",              # 「센터에서」
        "참여 학생은, 현장실습 연계 사업",                   # 쉼표
        "가" * 40 + " 나" * 6 + " 사업",                       # 너무 길다
        "학생들을 위한 진로 설계 지원사업",                  # 「학생들을」 대신 「위한」
    ]
    for f in fragments:
        assert programs.fragment_name(f), f
    names = ["산학연 공동기술개발 컨소시엄 사업", "스마트 해양 인재양성 거점 사업", "XRC 3.0 고도화 사업",
             "지역혁신 인재양성 체계(XRC) 사업", "평생학습 중심대학 지원사업", "학습제도 개선 사업"]
    for n in names:
        assert not programs.fragment_name(n), n


def test_mentions_skip_fragments_and_leading_cuts():
    text = "또한 2018년부터 시행된 해양레저 인재 양성사업 \\n바다과학 교육혁신 지원 사업"
    names, _a, _p = programs._mentions(text)
    assert not any(n.startswith(("또한", "n")) for n in names)
    assert programs.clean_label("n바다과학 교육혁신 지원 사업") == "바다과학 교육혁신 지원 사업"
    assert programs.clean_label("학 공동연구 컨소시엄사업") == "공동연구 컨소시엄사업"
    assert programs.clean_label("가 사업") == "가 사업"                       # 너무 짧아지면 두지 않는다


def test_alias_list_caps_and_dedupes():
    names = ["미래 해양 인재 사업", "미래해양인재사업", "따라서 미래 해양 인재 사업"] + [f"해양 인재 사업 {i}형" for i in range(20)]
    shown, more = programs.alias_list(names, ["MOI"], limit=5)
    assert shown[0] == "MOI" and len(shown) == 5 and more > 0
    assert not any(s.startswith("따라서") for s in shown)
    assert sum(1 for s in shown if s.replace(" ", "") == "미래해양인재사업") <= 1


# ── 절: 자리표시·겹친 로마 숫자·붙은 머리말 ────────────────────────────────
def test_placeholder_titles_and_heading_cleanup():
    for t in ("Ⅲ. 0000년 실험실 구축 개요", "ㅇㅇㅇ전문대학", "○○학과 운영 현황", "OO 센터 현황", "1. 20XX년 계획", "△△ 기관 협약"):
        assert sections.placeholder_title(t), t
    for t in ("Ⅲ. 2024년 실험실 구축 개요", "1. 추진 배경", "2-1. 운영 성과(100명)", "OECD 비교"):
        assert not sections.placeholder_title(t), t
    assert sections.clean_heading("ⅠⅠ. 대학의 여건") == "Ⅱ. 대학의 여건"
    assert sections.clean_heading("ⅢⅢ. 추진 계획") == "Ⅲ. 추진 계획"
    run = {"바다과학 인재양성 혁신 사업"}
    assert sections.clean_heading("Ⅱ. 대학의 여건바다과학 인재양성 혁신사업", run) == "Ⅱ. 대학의 여건"
    assert sections.clean_heading("2. 세부 바다과학 인재양성 혁신 사업", run) == "2. 세부 바다과학 인재양성 혁신 사업"   # 띄어 쓴 낱말은 둔다


def test_build_skips_placeholder_sections():
    chunks = [{"seq": 1, "kind": "heading", "content": "Ⅰ. 사업 개요"},
              {"seq": 2, "kind": "heading", "content": "ㅁㅁ대학교"},
              {"seq": 3, "kind": "heading", "content": "Ⅱ. 0000년 장비 구축 개요"},
              {"seq": 4, "kind": "text", "content": "본문"},
              {"seq": 5, "kind": "heading", "content": "ⅠⅠ. 추진 계획"}]
    titles = [s.title for s in sections.build(chunks)]
    assert "Ⅱ. 0000년 장비 구축 개요" not in titles and "Ⅱ. 추진 계획" in titles and "Ⅰ. 사업 개요" in titles


# ── 성과지표 이름 끝 각주 ─────────────────────────────────────────────────
def test_indicator_footnote_marks_are_dropped():
    assert indicators.clean_name("취업인원 7)") == "취업인원"
    assert indicators.clean_name("중급 수료자 12)") == "중급 수료자"
    assert indicators.clean_name("만족도²") == "만족도"
    assert indicators.clean_name("이수율(2)") == "이수율(2)"                 # 짝이 맞는 괄호는 이름
    assert indicators.name_key("취업인원 7)") == indicators.name_key("취업인원")


def test_match_degree_hides_internal_scores():
    assert match_degree("제목 같음·맥락 점수 0.72") == "제목 같음 · 일치 정도 높음"
    assert match_degree("흔한 제목·본문 맥락 점수 0.40") == "흔한 제목·본문 · 일치 정도 보통"
    assert match_degree("사례 본문 겹침 0.20").endswith("일치 정도 낮음")
    assert match_degree("제목이 두 문서에서 하나뿐") == "제목이 두 문서에서 하나뿐"


# ── 그래프 탐색: 이름·상태·연차·문서 수 ────────────────────────────────────
def _graph(db):
    kg_store.ensure(db)
    ids = [db.add_document(f"d{i}.hwp", f"x/{i}.hwp", doc_type="grant", access_level="public") for i in range(5)]
    hidden = db.add_document("h.hwp", "x/h.hwp", doc_type="grant", owner="other", access_level="owner")
    with db._conn() as conn:
        kg_store.put_nodes(conn, [
            ("group:G", "program_group", "해양 인재 체계(앵커)"),
            ("program:A", "program", "해양 인재 체계(앵커)", {"names": ["해양 인재 체계(앵커)"], "acronyms": []}),
            ("program:B", "program", "해양인재체계 (앵커)", {"names": [], "acronyms": []}),
            ("program:Z", "program", "빈 사업", {}),
            ("program:C", "program", "n바다 교육혁신 지원 사업", {}),
            ("year:A:r3", "year", "해양 인재 체계 3차년도", {"round": 3, "year": 2025}),
            (f"doc:{ids[0]}", "doc", "공고", {"kind_label": "notice", "status": "auto", "round": 3, "year": 2025}, ids[0]),
            (f"doc:{ids[1]}", "doc", "계획", {"status": "review"}, ids[1]),
            (f"doc:{ids[2]}", "doc", "계획2", {}, ids[2]),
            (f"doc:{ids[3]}", "doc", "계획3", {}, ids[3]),
            (f"doc:{hidden}", "doc", "남의 것", {}, hidden),
            ("doc:99999", "doc", "지운 문서", {}, 99999),
            (f"doc:{ids[0]}:sec:1", "section", "ㅇㅇㅇ대학교", {}, ids[0]),
            (f"doc:{ids[0]}:sec:2", "section", "ⅠⅠ. 추진 계획", {}, ids[0]),
        ])
        kg_store.put_edges(conn, [
            ("group:G", "program:A", "contains", "분류", ["장부"]),
            ("program:A", "year:A:r3", "contains", "분류", ["연차"]),
            ("year:A:r3", f"doc:{ids[0]}", "contains", "분류", ["제목"]),
            ("year:A:r3", f"doc:{hidden}", "contains", "분류", ["제목"]),
            ("year:A:r3", "doc:99999", "contains", "분류", ["제목"]),
            ("program:B", f"doc:{ids[1]}", "contains", "분류", ["제목"]),
            ("program:C", f"doc:{ids[2]}", "contains", "분류", ["제목"]),
            (f"doc:{ids[0]}", f"doc:{ids[0]}:sec:1", "contains", "분류", ["목차"]),
            (f"doc:{ids[0]}", f"doc:{ids[0]}:sec:2", "contains", "분류", ["목차"]),
        ])
    return ids, hidden


def test_explore_program_list_names_counts_and_groups(tmp_path):
    db = Database(tmp_path / "t.db")
    ids, hidden = _graph(db)
    kg_explore._cache.clear()
    staff = {"role": "staff", "user": "me", "dept": None}
    rows = {p["label"]: p for p in kg_explore.programs(db, staff)}
    assert "해양 인재 체계(앵커) (묶음)" in rows and rows["해양 인재 체계(앵커) (묶음)"]["type"] == "program_group"
    plain = [p for p in rows.values() if p["type"] == "program" and p["label"].startswith("해양")]
    assert len(plain) == 1 and plain[0]["n_docs"] == 2 and set(plain[0]["ids"]) == {"program:A", "program:B"}
    assert "빈 사업" not in rows                                              # 문서 0건은 목록에 없다
    assert "바다 교육혁신 지원 사업" in rows                                    # 앞 잘림 다듬기
    # 지운 문서·볼 수 없는 문서는 세지 않는다 — 개발자는 볼 수 있는 것까지, 지운 것은 빼고
    assert kg_explore.program_doc_sets(db, staff)["program:A"] == {ids[0]}
    assert kg_explore.program_doc_sets(db, {"role": "dev"})["program:A"] == {ids[0], hidden}


def test_explore_summary_uses_korean_labels_and_hides_placeholder_sections(tmp_path):
    db = Database(tmp_path / "t.db")
    ids, _h = _graph(db)
    staff = {"role": "staff", "user": "me", "dept": None}
    d = kg_explore.neighbors(db, f"doc:{ids[0]}", staff)
    info = d["center"]["info"]
    assert info["status"] == "자동 분류" and info["when"] == "3차년도 (2025)" and "round" not in info
    assert info["kind_label"] != "notice"
    labels = {n["label"] for n in d["nodes"]}
    assert "ㅇㅇㅇ대학교" not in labels and "Ⅱ. 추진 계획" in labels
    assert kg_explore.neighbors(db, f"doc:{ids[1]}", staff)["center"]["info"]["status"] == "검토 필요"


# ── 사업 그래프: 쪽 나눔·별칭 상한 ─────────────────────────────────────────
def test_program_view_caps_cells_and_pages_year(tmp_path):
    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    n = CELL_MAX + 5
    ids = [db.add_document(f"p{i}.hwp", f"x/p{i}.hwp", doc_type="grant", access_level="public") for i in range(n)]
    long_aliases = [f"아울러 {2000 + i}년부터 추진된 별빛 사업" for i in range(30)] + ["별빛 인재 사업"]
    with db._conn() as conn:
        kg_store.put_nodes(conn, [("program:S", "program", "별빛 인재 사업", {"names": long_aliases, "acronyms": ["SBX"]}),
                                  ("year:S:r1", "year", "별빛 인재 사업 1차년도", {"round": 1, "year": 2024})]
                           + [(f"doc:{d}", "doc", f"계획서 {i}", {"kind": "plan"}, d) for i, d in enumerate(ids)])
        kg_store.put_edges(conn, [("program:S", "year:S:r1", "contains", "분류", ["연차"])]
                           + [("year:S:r1", f"doc:{d}", "contains", "분류", ["제목"]) for d in ids])
    data = program_view(db, "program:S", scope={"role": "dev", "user": "zzdev", "dept": None})
    col = data["cols"][0]
    assert len(col["cells"]["plan"]) == CELL_MAX and col["more"]["plan"] == 5 and col["label"] == "1차년도 (2024)"
    assert data["aliases"] == ["SBX"] and data["n_docs"] == n                  # 문장 조각 별칭은 하나도 남지 않는다
    yp = year_page(data, "year:S:r1", 1)
    assert yp["total"] == n and len(yp["docs"]) == n and yp["pages"] == 1


# ── 프로젝트 검색: 진행 중 사업·계획 연도·이름 묶기 ─────────────────────────
def test_ongoing_rule():
    now = 2026
    assert project_refs.ongoing((2025, 2027), set(), now)
    assert not project_refs.ongoing((2019, 2022), {2026}, now)               # 기간이 있으면 기간이 우선
    assert project_refs.ongoing(None, {2024, 2026}, now)
    assert not project_refs.ongoing(None, {2020, 2021}, now)
    assert not project_refs.ongoing(None, set(), now)


def test_browse_marks_ongoing_programs_future_years_and_merges_names(tmp_path):
    db = Database(tmp_path / "t.db")
    y = datetime.date.today().year
    made = {}
    for name, key in [(f"{y - 1}년 은하 연계 사업", f"program:g|{y - 1}"), (f"{y + 1}년 은하 연계 사업", f"program:g|{y + 1}"),
                      (f"{y - 6}년 옛별 사업", f"program:o|{y - 6}"), (f"{y - 5}년 옛별사업", f"program:o2|{y - 5}")]:
        pid = db.create_project("grant", name, owner="zzdev", archived=True, program=key)
        db.add_document(f"{key}.hwp", f"dgx://{key}.hwp", doc_type="grant", project_id=pid)
        made[key] = pid
    got = project_refs.browse(db, None, status="all")
    by = {it["id"]: it for it in got["items"]}
    assert by[made[f"program:g|{y - 1}"]]["ongoing"] and not by[made[f"program:o|{y - 6}"]]["ongoing"]
    assert by[made[f"program:g|{y + 1}"]]["future"] and "계획 연도" in by[made[f"program:g|{y + 1}"]]["when"]
    groups = {g["name"]: g for g in project_refs.group_items(got["items"])}
    assert groups["은하 연계 사업"]["ongoing"] and not groups["옛별 사업"]["ongoing"]
    assert len(groups["옛별 사업"]["rows"]) == 2                                # 띄어쓰기만 다른 이름은 한 카드


def test_archived_page_defaults_to_all_and_korean_states(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    y = datetime.date.today().year
    pid = db.create_project("grant", f"{y}년 은하 연계 사업", owner="zzdev", archived=True, program=f"program:g|{y}")
    db.add_document("a.hwp", "dgx://a.hwp", doc_type="grant", project_id=pid)
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    html = c.get("/projects/archived").text
    assert 'value="all" checked' in html and "진행 중 사업" in html and ">보관<" not in html
    assert "여기서 연결해도 원래 자료는 바뀌지 않습니다." in html and "문서 보기" in html and "자료 보기" not in html
    # 보관된 묶음 화면에는 입력줄이 없다 — 빈 대화 안내도 입력줄을 가리키지 않는다
    page = c.get(f"/project/{pid}").text
    assert "위 입력줄에서" not in page and "보관 해제 뒤 대화를 시작할 수 있습니다" in page


# ── 별칭 붙이기: 같은 제목 맥락이나 되풀이된 짝만 ───────────────────────────
def test_listed_programs_do_not_join_acronyms():
    assert programs.lists_other_acronym("QRX 및 별빛 역량 강화 지원사업", "SBK")
    assert not programs.lists_other_acronym("별빛 역량 강화 지원사업", "SBK")
    assert not programs.lists_other_acronym("QRX 3.0 고도화 사업", "QRX3.0")          # 판 표기만 다르면 같은 약칭
    _n, _a, pairs = programs._mentions("QRX 및 별빛 역량 강화 지원사업(SBK)")
    assert not any(s == "SBK" and "QRX" in l for l, s in pairs)


def test_head_only_pair_needs_title_context_or_two_documents():
    one = [{"id": 1, "filename": "QRX 사업 결과보고서.hwp", "head": "참고: 바다 연안 탐사 지원사업(OCX) 소개"},
           {"id": 2, "filename": "QRX 사업 계획서.hwp", "head": "QRX 사업 계획"}]
    cards = programs.build_cards(one)
    qrx = next(c for c in cards if "QRX" in {programs._acr(a) for a in c.acrs})
    assert "OCX" not in {programs._acr(a) for a in qrx.acrs}
    assert not any("연안" in n for n in qrx.names)
    # 같은 짝을 문서 두 건이 쓰면 그 짝(이름 ↔ 약칭)은 잇는다 — 그래도 QRX 카드에는 붙지 않는다
    two = one + [{"id": 3, "filename": "회의 자료.hwp", "head": "바다 연안 탐사 지원사업(OCX) 협의"},
                 {"id": 4, "filename": "협약 자료.hwp", "head": "바다 연안 탐사 지원사업(OCX) 협약"}]
    cards = programs.build_cards(two)
    ocx = [c for c in cards if "OCX" in {programs._acr(a) for a in c.acrs}]
    assert ocx and any("연안" in n for n in ocx[0].names) and "QRX" not in {programs._acr(a) for a in ocx[0].acrs}


def test_alias_list_drops_other_programs_acronyms_and_tidies():
    shown, _m = programs.alias_list(["QRX 사업", "OCX 사업", "별빛 ( SBK ) 사업"], ["QRX", "OCX"], foreign={"OCX"})
    assert "OCX" not in shown and "OCX 사업" not in shown and "QRX" in shown
    assert "별빛 (SBK) 사업" in shown and not any("( SBK )" in x for x in shown)   # 괄호 안 띄어쓰기 정리


def test_procedure_only_program_hidden_and_shacl_examples_get_labels(tmp_path):
    db = Database(tmp_path / "t.db")
    ids, hidden = _graph(db)
    with db._conn() as conn:
        kg_store.put_nodes(conn, [("program:V", "program", "수행 정산 사업", {})])
        kg_store.put_edges(conn, [("program:V", f"doc:{ids[3]}", "contains", "분류", ["제목"])])
    kg_explore._cache.clear()
    labels = {p["label"] for p in kg_explore.programs(db, {"role": "dev"})}
    assert "수행 정산 사업" not in labels                                     # 절차 일반어뿐인 「사업」 이름
    staff = {"role": "staff", "user": "me", "dept": None}
    got = kg_explore.example_labels(db, [{"message": "m", "n": 2, "examples": [f"doc%3A{ids[0]}", f"doc%3A{hidden}"]}], staff)
    ex = got[0]["examples"]
    assert ex[0] == {"id": f"doc:{ids[0]}", "label": "공고"} and ex[1]["label"] == "볼 수 없는 문서"


def test_ontology_tab_only_for_developers(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    assert 'data-mode="onto"' not in c.get("/graph/explore").text


def test_alias_filter_on_stored_card_shape():
    """저장된 카드 꼴 그대로 — 약칭 목록에 남의 약칭·오기가 섞여 있어도(그래프를 다시 짓기 전) 화면 별칭에서는 빠진다."""
    prog = {"id": "program:qrx", "label": "별빛 산학 선도(QRX+) 육성사업",
            "props": {"acronyms": ["OCX", "HARBOR", "QRX+", "QRG+", "QX+", "QRX"], "names": ["별빛 산학 선도(QRX+) 육성사업"]}}
    others = [{"id": "program:ocx", "label": "바다 연안 탐사 지원사업", "props": {"acronyms": ["OCX"]}},
              {"id": "program:바다항만", "label": "바다 항만 인재체계", "props": {"acronyms": []}}]
    ledger = {"programs": [{"name": "바다 항만 인재체계", "terms": ["HARBOR"], "sources": ["https://example.org"]}]}
    canon, foreign, allowed = programs.alias_filters(prog, others, ledger)
    assert {"QRX", "QRX+"} <= canon and {"OCX", "HARBOR"} <= foreign
    shown, _m = programs.alias_list(prog["props"]["names"], prog["props"]["acronyms"], foreign=foreign, canonical=canon, allowed=allowed)
    assert set(shown) & {"OCX", "HARBOR", "QRG+", "QX+"} == set() and "QRX+" in shown
    # 장부가 그 사업 표기로 적었으면 오기처럼 보여도 둔다
    canon, foreign, allowed = programs.alias_filters(prog, others, {"programs": [{"name": "별빛 산학 선도(QRX+) 육성사업", "terms": ["QRG+"],
                                                                                 "sources": ["https://example.org"]}]})
    shown, _m = programs.alias_list([], prog["props"]["acronyms"], foreign=foreign, canonical=canon, allowed=allowed)
    assert "QRG+" in shown and "QX+" not in shown


def test_search_results_use_display_rules(tmp_path):
    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    with db._conn() as conn:
        kg_store.put_nodes(conn, [("unit:a", "unit", ". QRX 비전 및 목표"), ("unit:b", "unit", "QRX 비전 및 목표"),
                                  ("unit:c", "unit", "년 QRX운영위원회"), ("program:z", "program", "학년도 QRX 연계 사업"),
                                  ("program:y", "program", "따라서 QRX 연계 사업")])
    labels = [h["label"] for h in kg_explore.search(db, "QRX", {"role": "dev"})]
    assert labels.count("QRX 비전 및 목표") == 1 and "QRX운영위원회" in labels and "QRX 연계 사업" in labels
    assert not any(l.startswith(("따라서", ".", "년 ", "학년도")) for l in labels)
