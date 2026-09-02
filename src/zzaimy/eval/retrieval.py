"""검색 평가 지표 — 브리프 축 B의 공식 지표 (Recall@k, MRR, nDCG@k).

베이스라인 대비 개선폭 측정에 쓰는 정본 구현. 스크립트(51·53 계열)와
논문 표가 모두 여기서 나온 수치를 쓴다 — 구현이 갈리면 수치도 갈린다.

입력 계약: ranked_lists[i]는 쿼리 i의 검색 결과 id 목록(상위부터),
gold_sets[i]는 쿼리 i의 정답 id 집합.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(
    ranked_lists: Sequence[Sequence[str]],
    gold_sets: Sequence[set[str]],
    k: int,
) -> float:
    """상위 k 안에 정답이 하나라도 든 쿼리의 비율 (정답 1개 체계 기준)."""
    if not ranked_lists:
        return 0.0
    hit = sum(
        1 for ranked, gold in zip(ranked_lists, gold_sets)
        if gold & set(ranked[:k])
    )
    return hit / len(ranked_lists)


def mrr(
    ranked_lists: Sequence[Sequence[str]],
    gold_sets: Sequence[set[str]],
) -> float:
    """첫 정답 순위의 역수 평균. 정답이 순위에 없으면 0."""
    if not ranked_lists:
        return 0.0
    total = 0.0
    for ranked, gold in zip(ranked_lists, gold_sets):
        for i, doc_id in enumerate(ranked, start=1):
            if doc_id in gold:
                total += 1.0 / i
                break
    return total / len(ranked_lists)


def ndcg_at_k(
    ranked_lists: Sequence[Sequence[str]],
    gold_sets: Sequence[set[str]],
    k: int,
) -> float:
    """이진 관련도 nDCG@k — 정답이 여러 개면 이상 순서 DCG로 정규화."""
    if not ranked_lists:
        return 0.0
    total = 0.0
    for ranked, gold in zip(ranked_lists, gold_sets):
        dcg = sum(
            1.0 / math.log2(i + 1)
            for i, doc_id in enumerate(ranked[:k], start=1)
            if doc_id in gold
        )
        ideal = sum(
            1.0 / math.log2(i + 1)
            for i in range(1, min(len(gold), k) + 1)
        )
        total += (dcg / ideal) if ideal > 0 else 0.0
    return total / len(ranked_lists)
