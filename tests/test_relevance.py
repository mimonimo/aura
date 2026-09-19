"""검색 연관성 하한과 그래프 연관 근거 — "억지로 끼운 연관"을 막는 규칙.

세 층을 각각 검증한다.
  · 어휘축   흔한 명사만 겹친 조각은 후보가 되지 않는다
  · 임베딩축 인덱스에서 계산한 무관 기준선 아래는 돌려주지 않는다
  · 재랭킹   1위보다 한참 못한 후보는 근거로 올리지 않는다
그리고 그래프의 모든 간선에는 이유(why)가 붙는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zzaimy.app.db import Database
from zzaimy.app.regulations import find_relevant, lexical_rank, split_regulation
from zzaimy.graph.build import build_graph
from zzaimy.graph.entities import (
    clean_title,
    cohesion,
    document_role,
    hub_cutoff,
    identify_program,
    merge_variants,
    mine_programs,
    mine_role_words,
)


def _chunk(heading: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(heading=heading, content=content)


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "r.db")


# --- 어휘축 ---


def test_common_nouns_alone_do_not_make_a_candidate(db):
    """'학생·지원'처럼 모든 조각에 나오는 명사만 겹치면 근거가 아니다."""
    texts = [f"학생 지원 업무의 {i}번째 절차를 정한다. 담당자는 학생 지원 기준을 확인한다."
             for i in range(40)]
    texts.append("휴학원을 제출한 학생의 등록금 반환은 학사 일정에 따라 처리한다.")
    doc = db.add_document("합성.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(doc, "합성", [_chunk("", t) for t in texts])

    hits = lexical_rank(db, "휴학원 등록금 반환 기준")
    assert hits, "판별력 있는 명사가 있으면 찾아야 한다"
    chunks = {c["id"]: c["content"] for c in db.list_regulation_chunks()}
    assert "휴학원" in chunks[hits[0]]
    # 흔한 명사만 겹치는 질의는 아무것도 돌려주지 않는다
    assert lexical_rank(db, "학생 지원") == []


def test_no_evidence_yields_empty_result_instead_of_a_stretch(db):
    doc = db.add_document("장학.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(doc, "장학규정", split_regulation(
        "제1조(목적) 이 규정은 교내 장학금 지급 기준을 정함을 목적으로 한다.\n"
        "제2조(대상) 장학금은 직전 학기 성적이 우수한 재학생에게 지급한다.\n"
        "제3조(신청) 장학금 신청은 매 학기 개시 전에 접수한다."))
    assert find_relevant(db, "소방 설비 점검 주기와 과태료 부과 절차") == []


# --- 임베딩축 ---


def test_dense_floor_is_measured_from_the_index_not_hardcoded(monkeypatch):
    """무관 기준선은 적재된 벡터에서 계산한다 — 분포가 바뀌면 기준선도 바뀐다."""
    np = pytest.importorskip("numpy")
    from zzaimy.app import embed_search as es

    idx = es.EmbedIndex()
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(200, 16)).astype("float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    idx._ids, idx._vectors, idx._model = np.arange(200), vecs, object()
    monkeypatch.setattr(idx, "_load", lambda: True)
    floor = idx.noise_floor()
    assert floor is not None
    sims = vecs @ vecs.T
    off = sims[~np.eye(200, dtype=bool)]
    assert off.mean() < floor <= off.mean() + 1.2 * off.std()


def test_dense_floor_drops_results_below_the_line(monkeypatch):
    np = pytest.importorskip("numpy")
    from zzaimy.app import embed_search as es

    idx = es.EmbedIndex()
    vecs = np.eye(3, dtype="float32")
    idx._ids, idx._vectors = np.array([10, 11, 12]), vecs
    idx._model = SimpleNamespace(
        encode=lambda texts, normalize_embeddings=True: np.array([[1.0, 0.0, 0.0]], "float32"))
    monkeypatch.setattr(idx, "_load", lambda: True)
    assert idx.search("q", 3, min_sim=0.5) == [(10, 1.0)]     # 나머지는 코사인 0
    assert len(idx.search("q", 3, min_sim=0.0)) == 3          # 하한을 끄면 전부


# --- 재랭킹 ---


def test_rerank_prunes_the_tail_only_when_asked(monkeypatch):
    from zzaimy.app import rerank

    chunks = [{"heading": h, "content": h} for h in ("가", "나", "다")]
    monkeypatch.setattr(rerank, "_encoder",
                        lambda: SimpleNamespace(
                            predict=lambda pairs, show_progress_bar=False: [0.02, 0.9, 0.5]))
    assert [c["heading"] for c in rerank.rerank_chunks("q", list(chunks))] == ["나", "다", "가"]
    pruned = rerank.rerank_chunks("q", list(chunks), prune=True)
    assert [c["heading"] for c in pruned] == ["나", "다"]      # 1위의 25% 미만은 제외


def test_weak_evidence_keeps_one_hit_instead_of_silently_dropping_all(monkeypatch):
    """하한을 넘는 후보가 없어도 1위는 남기고 '근거가 약하다'고 표시한다."""
    from zzaimy.app.rerank import prune_scored

    monkeypatch.setenv("ZZAIMY_RERANK_MIN", "0.9")
    a, b = {"heading": "가"}, {"heading": "나"}
    kept, weak = prune_scored([(a, 0.2), (b, 0.1)])
    assert kept == [a] and weak is True
    monkeypatch.delenv("ZZAIMY_RERANK_MIN")
    kept, weak = prune_scored([(a, 0.9), (b, 0.8)])
    assert kept == [a, b] and weak is False


def test_find_relevant_marks_weak_evidence(db, monkeypatch):
    from zzaimy.app import rerank

    doc = db.add_document("장학.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(doc, "장학규정", split_regulation(
        "제1조(목적) 이 규정은 교내 장학금 지급 기준을 정함을 목적으로 한다.\n"
        "제2조(대상) 장학금은 직전 학기 성적이 우수한 재학생에게 지급한다.\n"
        "제3조(신청) 장학금 신청은 매 학기 개시 전에 접수한다."))
    monkeypatch.setenv("ZZAIMY_RERANK_MIN", "0.9")
    monkeypatch.setattr(rerank, "_encoder",
                        lambda: SimpleNamespace(
                            predict=lambda pairs, show_progress_bar=False: [0.1] * len(pairs)))
    hits = find_relevant(db, "장학금 지급 대상")
    assert len(hits) == 1 and hits[0]["weak_evidence"] is True


def test_rerank_absolute_floor_can_declare_no_evidence(monkeypatch):
    from zzaimy.app import rerank

    monkeypatch.setenv("ZZAIMY_RERANK_MIN", "0.5")
    monkeypatch.setattr(rerank, "_encoder",
                        lambda: SimpleNamespace(
                            predict=lambda pairs, show_progress_bar=False: [0.1, 0.2]))
    chunks = [{"heading": "가", "content": "가"}, {"heading": "나", "content": "나"}]
    assert rerank.rerank_chunks("q", chunks, prune=True) == []


# --- 사업 정체 ---


def test_title_cleaning_strips_filing_marks():
    assert clean_title("(붙임5) 2026년 인문사회 융합인재양성사업 사업계획서 양식.hwp") == \
        "인문사회 융합인재양성사업 사업계획서 양식"
    assert clean_title("붙임1. ★241106_2024년 단계평가 추진계획.hwpx") == "단계평가 추진계획"


PROGRAM_TITLES = {
    1: "(공고문) 2026년 상상사업(IMAG) 컨소시엄 선정 공고.hwpx",
    2: "(붙임1) 2026년 상상사업(IMAG) 기본계획.pdf",
    3: "붙임3. 상상사업(IMAG) 본신청서 양식.hwpx",
    4: "붙임5. 상상사업 사업계획서 양식.hwp",
    5: "2026년 나눔대학 지정 신청 공고.pdf",
    6: "붙임2. 나눔대학 지정계획.pdf",
    7: "붙임3. 나눔대학 예비지정 신청서 작성지침.hwpx",
}
# 본문 — 사업명이 조사와 함께 되풀이해서 쓰인다(하나의 이름으로 불린다)
PROGRAM_BODIES = {
    1: "상상사업(IMAG)은 융합 인재를 기른다. 상상사업(IMAG)의 신청 기한은 다음과 같다.",
    2: "상상사업(IMAG)의 기본 방향이다. 상상사업(IMAG)은 컨소시엄으로 운영한다.",
    3: "상상사업(IMAG)에 신청한다. 상상사업(IMAG)의 신청서 양식이다.",
    4: "상상사업의 사업계획서다. 상상사업은 연차 점검을 받는다.",
    5: "나눔대학은 지역과 협력한다. 나눔대학의 지정 절차를 정한다.",
    6: "나눔대학의 지정 계획이다. 나눔대학은 예비지정을 거친다.",
    7: "나눔대학 예비지정 신청서를 쓴다. 나눔대학의 작성 지침이다.",
}


def test_program_identity_is_mined_from_repeated_title_cores():
    titles = PROGRAM_TITLES
    programs = mine_programs(titles, PROGRAM_BODIES)
    names = {p.name for p in programs.values()}
    assert "상상사업(IMAG)" in names and "나눔대학" in names
    # 역할어가 붙은 긴 어구는 별개의 사업이 되지 않는다
    assert not any("양식" in n or "공고" in n or "기본계획" in n for n in names)
    # 괄호 약칭이 빠진 표기도 같은 사업으로 묶인다
    key4 = identify_program(programs, titles[4])
    key1 = identify_program(programs, titles[1])
    assert key4 and key1 and key4[0] == key1[0]


def test_title_pattern_without_body_support_is_not_a_program():
    """파일명 작명 습관은 사업이 아니다 — 본문이 뒷받침하지 않으면 만들지 않는다.

    실측(운영 DB 교내문서 39건): 제목에서 캔 후보 18개 중 14개가 본문 등장 0건인
    문서 종류·학과 이름이었다("학년도 입학자 연계교육과정 편성표 학과 계열").
    """
    titles = {
        i: f"{2020 + i}학년도_입학자_연계교육과정_편성표_학과_계열_{d}_연계편입.pdf"
        for i, d in enumerate(["건축과", "전기자동화과", "소프트웨어융합과", "건축과"])
    }
    bodies = {i: "학과 전공 교과목 이수 학점 표이다. 편성 내용은 표와 같다." for i in titles}
    assert mine_programs(titles) != {}          # 제목만 보면 후보가 잡히고
    assert mine_programs(titles, bodies) == {}  # 본문이 뒷받침하지 않으면 채굴 0건


def test_organization_and_time_expressions_are_not_programs():
    titles = {
        1: "영남이공대학교 학칙.pdf", 2: "영남이공대학교 규정집.pdf",
        3: "2026년 2학기 장학 계획.pdf", 4: "2025년 2학기 장학 계획.pdf",
    }
    bodies = {
        1: "영남이공대학교는 다음과 같이 정한다. 영남이공대학교의 학칙이다.",
        2: "영남이공대학교의 규정이다. 영남이공대학교는 이를 공고한다.",
        3: "2학기의 일정이다. 2학기는 9월에 시작한다. 2학기에 신청한다.",
        4: "2학기의 계획이다. 2학기는 9월에 시작한다. 2학기에 접수한다.",
    }
    names = {p.name for p in mine_programs(titles, bodies).values()}
    assert "영남이공대학교" not in names      # 기관은 사업이 아니다
    assert "2학기" not in names               # 기간 표현은 사업이 아니다


def test_ocr_corrupted_entity_merges_into_the_frequent_form():
    """앞 글자가 오염된 같은 기관을 따로 세지 않는다(실측: 한국장학재단 / 기한국장학재단)."""
    counts = {
        ("한국장학재단", "org"): 12, ("기한국장학재단", "org"): 3,
        ("한국장학재단b", "org"): 2, ("국가우수장학재단", "org"): 4,
    }
    canon = merge_variants(counts)
    assert canon[("기한국장학재단", "org")] == "한국장학재단"
    assert canon[("한국장학재단b", "org")] == "한국장학재단"
    # 길이 차가 큰 별개 이름은 합치지 않는다
    assert canon[("국가우수장학재단", "org")] == "국가우수장학재단"


def test_role_words_are_mined_not_listed():
    titles = {
        1: "상상사업 공고문.hwp", 2: "나눔대학 공고문.pdf",
        3: "상상사업 사업계획서 양식.hwp", 4: "나눔대학 사업계획서 양식.hwp",
        5: "상상사업 기본계획.pdf", 6: "나눔대학 기본계획.pdf",
    }
    roles = mine_role_words(titles)
    assert {"공고문", "양식", "기본계획"} <= set(roles)
    assert "상상사업" not in roles and "나눔대학" not in roles
    assert document_role(titles[3], roles) == "양식"


def test_hub_and_cohesion_rules_are_corpus_derived():
    assert hub_cutoff(133) == 11          # √N — 개체 하나가 잇는 문서 수 상한
    assert hub_cutoff(4) == 2
    assert cohesion(33, 133) < 0.5        # 평가위원회: '평가'가 밖에서도 흔하다
    assert cohesion(62, 62) >= 0.5        # 한국연구재단: 수식부가 이 이름에만 쓰인다


# --- 그래프 간선의 이유 ---


def test_every_graph_edge_carries_a_reason(db):
    for name, body in (
        ("2026년 상상사업(IMAG) 공고문.pdf",
         "제1조(목적) 상상사업(IMAG)의 선정 절차를 정한다. 「고등교육법」 제7조에 따른다."),
        ("붙임1. 상상사업(IMAG) 기본계획.pdf",
         "제1조(추진방향) 상상사업(IMAG)의 기본 방향을 정한다. 「고등교육법」 제7조에 따른다."),
        ("붙임3. 상상사업(IMAG) 사업계획서 양식.hwp",
         "제1조(서식) 상상사업(IMAG) 사업계획서의 작성 서식을 정한다."),
    ):
        doc = db.add_document(name, "/x", doc_type="regulation")
        db.add_regulation_chunks(doc, name, split_regulation(body))
    g = build_graph(db, include_similarity=False)
    assert g["edges"], "간선이 하나는 나와야 한다"
    assert all(e.get("why") for e in g["edges"]), "이유 없는 간선이 있다"
    kinds = {e["kind"] for e in g["edges"]}
    assert {"of_program", "same_program"} <= kinds
    program_nodes = [n for n in g["nodes"] if n.get("doc_type") == "program"]
    assert [n["label"] for n in program_nodes] == ["상상사업(IMAG)"]
    same = [e for e in g["edges"] if e["kind"] == "same_program"][0]
    assert "상상사업(IMAG)" in same["why"] and same["evidence"]


def test_documents_of_different_programs_are_not_linked(db):
    for name, body in (
        ("상상사업(IMAG) 공고문.pdf", "제1조(목적) 상상사업(IMAG)을 정한다. " * 4),
        ("나눔대학 공고문.pdf", "제1조(목적) 나눔대학 지정 절차를 정한다. " * 4),
    ):
        doc = db.add_document(name, "/x", doc_type="regulation")
        db.add_regulation_chunks(doc, name, split_regulation(body))
    g = build_graph(db, include_similarity=False)
    doc_pairs = [(e["s"], e["t"]) for e in g["edges"]
                 if e["s"].startswith("d") and e["t"].startswith("d")]
    assert doc_pairs == []
