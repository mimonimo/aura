"""corpus_pilot.db 하이브리드 검색 — Kiwi 희소 + KURE dense RRF (/dev/corpus).

플랫폼 KURE 모델(embed_search._index)을 재사용하고 코퍼스 전용 npz만 따로
로드한다. 임베딩(npz)이 없으면 희소검색만 반환한다(graceful).
"""
from __future__ import annotations
import threading
from pathlib import Path

_CORPUS_NPZ = Path("data/platform/corpus_pilot_embeddings.npz")
_lock = threading.Lock()
_ids = None
_vecs = None
_failed = False
_floor = None


def _load():
    global _ids, _vecs, _failed
    if _failed:
        return False
    if _vecs is not None:
        return True
    with _lock:
        if _vecs is not None:
            return True
        try:
            import numpy as np
            if not _CORPUS_NPZ.exists():
                return False
            d = np.load(_CORPUS_NPZ)
            _ids = d["ids"]
            _vecs = d["vectors"]
            return True
        except Exception:
            _failed = True
            return False


def corpus_floor():
    """코퍼스 인덱스의 무관 기준선 — 무작위 조각 쌍 코사인의 평균 + k·표준편차.

    플랫폼 인덱스와 같은 규칙(embed_search 참조). 코퍼스마다 잡음 바닥이 다르므로
    코퍼스 벡터로 따로 잰다.
    """
    global _floor
    if _floor is not None:
        return _floor
    import os

    from zzaimy.app.embed_search import DENSE_FLOOR_K, _FLOOR_SAMPLE_PAIRS

    k = float(os.environ.get("ZZAIMY_DENSE_FLOOR_K", DENSE_FLOOR_K))
    if k <= 0 or not _load():
        return None
    try:
        import numpy as np

        n = len(_vecs)
        if n < 50:
            return None
        rng = np.random.default_rng(0)
        a, b = rng.integers(0, n, _FLOOR_SAMPLE_PAIRS), rng.integers(0, n, _FLOOR_SAMPLE_PAIRS)
        m = a != b
        sims = np.einsum("ij,ij->i", _vecs[a[m]], _vecs[b[m]])
        _floor = float(sims.mean() + k * sims.std())
    except Exception:
        return None
    return _floor


def corpus_dense(query, top_k=20, min_sim=None):
    """(chunk_id, 코사인) — 무관 기준선 미만은 돌려주지 않는다."""
    if not _load():
        return []
    from zzaimy.app.embed_search import _index
    if not _index._load():
        return []
    # 질의 벡터는 플랫폼 색인과 같은 경로로 — 서빙 장비 서비스가 먼저, 없으면 VM 모델, 둘 다 없으면 조밀 축 없이
    # (실측 2026-09-22: 서비스만 쓰는 구성에서 VM 모델이 None 이라 코퍼스 탭 검색이 죽었다)
    qvs = _index._encode([query])
    if qvs is None:
        return []
    qv = qvs[0]
    sims = _vecs @ qv
    order = sims.argsort()[-top_k:][::-1]
    out = [(int(_ids[i]), float(sims[i])) for i in order]
    floor = corpus_floor() if min_sim is None else min_sim
    return out if floor is None else [(i, s) for i, s in out if s >= floor]


def corpus_hybrid_search(cdb, query, top_k=15):
    """희소(Kiwi) + dense(KURE) RRF 병합 → 품질 필터 → 리랭크 꼬리 자르기.

    dense가 없으면 희소만 쓴다. 어느 축도 기준을 넘지 못하면 빈 목록 —
    근거가 없다는 사실을 그대로 돌려준다(억지로 채우지 않는다).
    """
    from zzaimy.app.regulations import select_candidates, sparse_search
    from zzaimy.app.rerank import prune_scored, rerank_scored
    sparse = sparse_search(cdb, query, top_k=20)
    dense = corpus_dense(query, 20)
    by_id = {c["id"]: c for c in cdb.list_regulation_chunks()}
    lexical_ids = [c["id"] for c in sparse][:20]
    if dense:
        from zzaimy.app.embed_search import rrf_merge
        dense_ids = [i for i, _ in dense if i in by_id]
        merged = rrf_merge(lexical_ids, dense_ids, w_a=0.4, w_b=1.0)
    else:
        merged = lexical_ids
    candidates = select_candidates(merged, by_id, limit=max(top_k, 10))
    if not candidates:
        return []
    scored = rerank_scored(query, candidates)
    if scored is None:
        return candidates[:top_k]
    kept, weak = prune_scored(scored)
    out = []
    for c in kept[:top_k]:
        item = dict(c)
        if weak:
            item["weak_evidence"] = True
        out.append(item)
    return out
