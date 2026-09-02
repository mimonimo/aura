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
