"""규정 저장소 테스트 (외부/내부 관리 규정 근거 검토).

에이전트가 규정을 근거로 검토 의견을 내기 위한 계층. 데이터는 전부 합성.
"""

from zzaimy.app.db import Database
from zzaimy.app.regulations import compose_review_context, find_relevant, split_regulation

ARTICLE_STYLE = """제1조(목적) 이 규정은 합성사업의 운영 기준을 정함을 목적으로 한다.
제2조(정의) 이 규정에서 평가위원이란 선정평가를 수행하는 자를 말한다.
제3조(회피) 평가위원은 이해관계가 있는 대학의 평가를 회피하여야 한다."""

MANUAL_STYLE = (
    "목적\n교육부 재정지원사업의 기본 절차를 제시한다.\n\n"
    "평가위원 선정 및 관리\n평가위원은 외부 전문가로 구성하며 보안서약서를 제출한다.\n\n"
    "사업운영 및 사후관리\n사업비 집행은 정산 기준을 따르고 현장점검을 실시한다."
)


def test_split_article_style_by_article():
    chunks = split_regulation(ARTICLE_STYLE)
    assert len(chunks) == 3
    assert chunks[0].heading.startswith("제1조")
    assert "평가위원" in chunks[1].content


def test_split_manual_style_by_paragraph():
    chunks = split_regulation(MANUAL_STYLE)
    assert len(chunks) >= 3
    assert all(c.content.strip() for c in chunks)


def test_find_relevant_matches_topic(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="합성규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "합성규정", split_regulation(ARTICLE_STYLE))

    hits = find_relevant(db, "평가위원 회피 의무가 있는지 검토")
    assert hits
    assert any("회피" in h["content"] for h in hits)


def test_find_relevant_returns_empty_for_unrelated(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="합성규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "합성규정", split_regulation(ARTICLE_STYLE))
    assert find_relevant(db, "김치찌개 끓이는 순서") == []


def test_compose_review_context_cites_regulation(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="합성규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "합성규정", split_regulation(ARTICLE_STYLE))

    ctx = compose_review_context(db, "평가위원 위촉 계획과 이해관계 회피 기준을 다루는 문서")
    assert "합성규정" in ctx
    assert "회피" in ctx or "평가위원" in ctx


def test_compose_review_context_empty_when_no_regulations(tmp_path):
    db = Database(tmp_path / "t.db")
    assert compose_review_context(db, "아무 문서") == ""


def test_find_relevant_respects_sector(tmp_path):
    db = Database(tmp_path / "t.db")
    d1 = db.add_document(filename="채용규정.pdf", stored_path="/x", doc_type="regulation")
    db.add_regulation_chunks(d1, "채용규정", split_regulation(ARTICLE_STYLE), sector="recruit")
    d2 = db.add_document(filename="공통규정.pdf", stored_path="/y", doc_type="regulation")
    db.add_regulation_chunks(
        d2, "공통규정",
        split_regulation("제1조(공통) 평가위원 회피 의무는 모든 업무에 공통 적용된다."),
        sector="common",
    )
    # 입학 섹터 검토 → 채용 전용 규정은 빠지고 공통만 잡힌다
    hits = find_relevant(db, "평가위원 회피 의무 검토", sector="admission")
    assert hits
    assert all(h["sector"] == "common" for h in hits)
    # 채용 섹터 검토 → 채용 + 공통 둘 다 후보
    hits2 = find_relevant(db, "평가위원 회피 의무 검토", sector="recruit")
    assert {h["sector"] for h in hits2} <= {"recruit", "common"}
    assert any(h["sector"] == "recruit" for h in hits2)


def test_user_dictionary_keeps_domain_terms_whole():
    from zzaimy.app.regulations import extract_nouns

    nouns = extract_nouns("산학협력단 전공심화과정과 일학습병행 공동훈련센터 운영")
    assert "산학협력단" in nouns
    assert "전공심화과정" in nouns
    assert "일학습병행" in nouns
    assert "공동훈련센터" in nouns


def test_rerank_chunks_reorders_and_survives_failure(monkeypatch):
    """리랭커는 점수순 재정렬하되, 어떤 실패에도 검색을 죽이지 않는다."""
    from zzaimy.app import rerank

    chunks = [
        {"heading": "가", "content": "덜 관련"},
        {"heading": "나", "content": "가장 관련"},
        {"heading": "다", "content": "중간"},
    ]

    class FakeCE:
        def predict(self, pairs, show_progress_bar=False):
            return [0.1, 0.9, 0.5]

    monkeypatch.setattr(rerank, "_encoder", lambda: FakeCE())
    out = rerank.rerank_chunks("질문", list(chunks))
    assert [c["heading"] for c in out] == ["나", "다", "가"]

    class BoomCE:
        def predict(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(rerank, "_encoder", lambda: BoomCE())
    out2 = rerank.rerank_chunks("질문", list(chunks))
    assert [c["heading"] for c in out2] == ["가", "나", "다"]


def test_restore_spacing_joins_letter_spaced_ocr_output():
    """tesseract 사진 OCR 실측: '영 남 이 공 학교'처럼 글자마다 띄운 줄을 어절로 되돌린다."""
    from zzaimy.app.regulations import restore_spacing

    out = restore_spacing("위 사 람 은 영 남 이 공 대 학교 사 이 버 보 안 과 에서 우 수 한 성 적 으로 입 상 하였 기에 상 장 을 수 여 함")
    assert "영남이공대학교" in out.replace(" ", "")     # 글자 사이 공백이 사라지고
    assert " " in out and "위 사 람" not in out            # 어절 단위로 다시 띄어진다
    normal = "이 규정은 산학협력단의 운영 기준을 정함을 목적으로 한다."
    assert restore_spacing(normal) == normal             # 정상 문장은 그대로


def test_table_only_chunk_gets_heading_from_the_table_itself():
    """표가 본문인 조각(편성표·서식)은 표 머리에서 표제를 만든다 — 문서별 예외 없이."""
    from zzaimy.app.regulations import _heading_of

    plan = ("학과(계열) | 소프트웨어융합과\n연계편입 학과(전공) | 컴퓨터공학과\n"
            "학년 | 학기 | 연계교과목\n1 | 1 | IT와소프트웨어 | 컴퓨팅사고")
    assert _heading_of(plan) == "소프트웨어융합과 · 컴퓨터공학과"
    # 제목 줄이 앞에 붙어 있어도 표 행만 본다
    assert _heading_of("2025학년도 입학자\n\n연계교육과정 편성표\n\n" + plan).startswith("소프트웨어융합과")
    # 값 칸이 여럿인 서식 행은 이름 칸이 표제가 된다
    form = ("사업명 | 창업교육 혁신 선도대학(SCOUT) - SCOUT(STARTUP Co-Op University) -\n"
            "컨소시엄 주관대학 | 대학명 | 대학교 | 대학교")
    assert _heading_of(form) == "사업명 · 컨소시엄 주관대학"


def test_table_heading_never_promotes_prose_or_data_rows():
    """거짓 표제를 만들지 않는다 — 자료 행·서술문·표 한 줄에는 표제를 붙이지 않는다."""
    from zzaimy.app.regulations import _heading_of

    assert _heading_of("1 | 1 | IT와소프트웨어 | 컴퓨팅사고\n1 | 2 | 데이터베이스 | C기초") == ""
    assert _heading_of("이 규정은 2022년 5월 26일부터 시행한다. 다만 부칙은 예외로 한다.") == ""
    assert _heading_of("신청 자격은 다음과 같다. 재학생 수 기준을 충족해야 한다.\n구분 | 기준") == ""


def test_reranker_asks_serving_box_first_and_falls_back(monkeypatch):
    """리랭커는 서빙 장비(GPU)에 먼저 묻고, 실패하면 VM 쪽 경로로 물러난다."""
    import json

    from zzaimy.app import rerank

    chunks = [{"id": 1, "reg_title": "산학협력단 사무분장 규정", "heading": "제3조(업무분장)", "content": "가"},
              {"id": 2, "reg_title": "학칙", "heading": "제9조", "content": "나"}]
    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"scores": [-8.4, 3.1]}).encode()   # 서비스는 원시 로짓을 준다

    def fake_open(req, timeout=0):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setenv("ZZAIMY_RERANK_URL", "http://thor:8013/score")
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    got = rerank.rerank_scored("업무분장", chunks)
    assert [c["id"] for c, _ in got] == [2, 1]              # 점수 순서대로
    # 눈금은 0~1 — 하한(RERANK_MIN)·꼬리 자르기가 이 눈금을 전제한다
    assert all(0.0 <= sc <= 1.0 for _, sc in got)
    kept, weak = rerank.prune_scored(got)
    assert len(kept) >= 1 and not weak
    assert sent["body"]["max_length"] == rerank.REMOTE_MAX_LEN
    assert "산학협력단 사무분장 규정" in sent["body"]["texts"][0]   # 문서 이름까지 보낸다

    def boom(req, timeout=0):
        raise OSError("연결 거부")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr(rerank, "_encoder", lambda: None)
    assert rerank.rerank_scored("업무분장", chunks) is None   # 물러날 곳도 없으면 원래 순서


def test_query_embedding_uses_serving_box_and_falls_back(monkeypatch, tmp_path):
    """질의 임베딩은 서빙 장비(GPU)에서 만들고, 서비스가 죽으면 VM 쪽으로 물러난다."""
    import json

    import numpy as np

    from zzaimy.app import embed_search

    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            calls["n"] += 1
            return json.dumps({"vectors": [[0.6, 0.8]], "model": "KURE-v1"}).encode()

    monkeypatch.setenv("ZZAIMY_EMBED_URL", "http://thor:8014/embed")
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _Resp())
    got = embed_search.remote_vectors(["연구비 정산"])
    assert got is not None and calls["n"] == 1
    assert np.allclose(got[0], [0.6, 0.8])

    # 서비스가 죽으면 None — 호출부는 VM 모델이나 키위 검색으로 내려간다
    def boom(req, timeout=0):
        raise OSError("연결 거부")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert embed_search.remote_vectors(["연구비 정산"]) is None
    monkeypatch.delenv("ZZAIMY_EMBED_URL")
    assert embed_search.remote_vectors(["연구비 정산"]) is None      # 꺼져 있으면 묻지 않는다


def test_spacing_repair_needs_a_run_of_single_syllables():
    """글자 벌어짐은 '한 글자가 연달아' 나오는 것으로 판정한다 — 비율로 보면 정상 표현이 걸린다."""
    from zzaimy.app.regulations import _collapse_over_spacing, over_spacing_evidence

    spread = ("영 남 이 공 대 학교 산 학 협력단\n"
              "연 구 개 발 과 제 관리 규정\n"
              "ㅇ 계 좌 정보\n등 록 계좌")
    assert over_spacing_evidence(spread)
    fixed = _collapse_over_spacing(spread, short_too=True)
    assert "영남이공대학교" in fixed and "계좌정보" in fixed and "등록계좌" in fixed

    # 정상 문장은 건드리지 않는다 — '그 외 사항은'은 한 글자가 2연속뿐이다
    plain = "이 법은 시행한다\n그 외 사항은 따른다\n산학협력단의 업무는 다음과 같다"
    assert not over_spacing_evidence(plain)
    assert _collapse_over_spacing(plain, short_too=False) == plain
