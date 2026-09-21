"""평가 하네스 — 검색 지표 (브리프 축 B: Recall@k, MRR, nDCG)."""


def test_recall_at_k():
    from zzaimy.eval.retrieval import recall_at_k

    # 쿼리 2개: 정답이 각각 상위 3위, 순위 밖
    ranked = [["a", "b", "gold1"], ["x", "y", "z"]]
    golds = [{"gold1"}, {"gold2"}]
    assert recall_at_k(ranked, golds, k=3) == 0.5
    assert recall_at_k(ranked, golds, k=2) == 0.0


def test_mrr():
    from zzaimy.eval.retrieval import mrr

    ranked = [["gold1", "b"], ["x", "gold2"], ["x", "y"]]
    golds = [{"gold1"}, {"gold2"}, {"gold3"}]
    # 1/1, 1/2, 0 → 평균 0.5
    assert abs(mrr(ranked, golds) - 0.5) < 1e-9


def test_ndcg_at_k():
    from zzaimy.eval.retrieval import ndcg_at_k

    # 정답 1개가 1위면 1.0, 2위면 1/log2(3)
    assert abs(ndcg_at_k([["g"]], [{"g"}], k=5) - 1.0) < 1e-9
    import math
    expect = (1 / math.log2(3)) / 1.0
    assert abs(ndcg_at_k([["x", "g"]], [{"g"}], k=5) - expect) < 1e-9


def test_empty_inputs_are_zero():
    from zzaimy.eval.retrieval import mrr, ndcg_at_k, recall_at_k

    assert recall_at_k([], [], k=5) == 0.0
    assert mrr([], []) == 0.0
    assert ndcg_at_k([], [], k=5) == 0.0


def test_production_row_uses_the_production_candidate_count():
    """측정의 '운영 구성' 행은 운영과 같은 수의 후보를 리랭커에 넘겨야 한다 — 어긋나면 운영을 재현하지 못한다."""
    from zzaimy.app import regulations as reg
    from zzaimy.eval import retrieval_eval as rev

    assert rev.PRODUCTION_CANDIDATES == reg.CANDIDATE_LIMIT


def test_alt_dense_axis_is_measured_beside_production(monkeypatch):
    """대안 조밀 축(예: KURE-v2 다중 벡터)이 있으면 단독·어휘 융합·리랭커 행과 후보 진입률(R@20)을
    운영 행과 나란히 낸다 — ADR-0022 의 채택 조건을 같은 질의로 재기 위해서다."""
    from zzaimy.eval import retrieval_eval as rev

    queries = [rev.Query(f"질의 {i}", "practical", i) for i in range(6)]
    golds = [{i} for i in range(6)]
    ret = rev.Retrievers(
        lexical=lambda q: [99, 98],                        # 어휘는 늘 틀린다
        dense=lambda q: [98, 97],                          # 운영 임베딩도 틀린다
        hybrid=lambda q, lex, den: list(dict.fromkeys(den + lex)),
        rerank=lambda q, cand: cand,                       # 순서 유지
        alt_dense=lambda q: [int(q.split()[-1]), 97],      # 대안 축은 정답을 1위로
        alt_name="KURE-v2",
    )
    rows, notes = rev.evaluate(queries, golds, ret, rerank_sample=6)
    by = {r["method"]: r for r in rows}
    assert by["다중 벡터(KURE-v2)"]["recall_at_1"] == 1.0
    assert by["하이브리드(어휘+KURE-v2)"]["recall_at_20"] == 1.0 and by[rev.METHOD_HYBRID]["recall_at_20"] == 0.0
    assert by["어휘+KURE-v2+리랭커"]["recall_at_1"] == 1.0 and by[rev.METHOD_PRODUCTION]["recall_at_1"] == 0.0
    assert any("후보 진입률" in n for n in notes)
    monkeypatch.delenv("ZZAIMY_ALT_DENSE_URL", raising=False)
    assert rev.alt_dense_from_env() == (None, "")          # 주소가 없으면 대안 축 없음
