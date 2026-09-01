"""임베딩 검색 (KURE-v1) — 규정 조각 하이브리드 검색의 dense 축.

52_embed_chunks.py가 만든 사전 계산 벡터(npz)를 읽고, 질의만 실시간 임베딩한다.
모델·인덱스는 지연 로드하며, 준비물이 없으면 조용히 비활성(키위 검색만 동작).
P3에서 Qdrant로 이관하기 전까지의 인메모리 구현.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

INDEX_PATH = Path("data/platform/chunk_embeddings.npz")
MODEL_NAME = "nlpai-lab/KURE-v1"


class EmbedIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: object | None = None
        self._ids: object = None
        self._vectors: object = None
        self._failed = False

    def _load(self) -> bool:
        if self._failed:
            return False
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                import numpy as np
                from sentence_transformers import SentenceTransformer

                if not INDEX_PATH.exists():
                    raise FileNotFoundError(INDEX_PATH)
                data = np.load(INDEX_PATH)
                self._ids = data["ids"]
                self._vectors = data["vectors"]
                # vLLM이 GPU를 점유하므로 질의 임베딩은 CPU로
                self._model = SentenceTransformer(MODEL_NAME, device="cpu")
                log.info("임베딩 인덱스 로드: %d조각", len(data["ids"]))
                return True
            except Exception as e:
                log.warning("임베딩 검색 비활성 (%s: %s) — 키위 검색만 사용", type(e).__name__, e)
                self._failed = True
                return False

    def search(self, query: str, top_k: int = 12) -> list[tuple[int, float]]:
        """(chunk_id, 유사도) 상위 top_k. 비활성이면 빈 목록."""
        if not self._load():
            return []
        model = self._model
        ids, vectors = self._ids, self._vectors
        assert model is not None and ids is not None and vectors is not None
        qv = model.encode([query], normalize_embeddings=True)[0]  # type: ignore[attr-defined]
        sims = vectors @ qv  # type: ignore[operator]
        order = sims.argsort()[-top_k:][::-1]
        return [(int(ids[i]), float(sims[i])) for i in order]  # type: ignore[index]


_index = EmbedIndex()


def embed_search(query: str, top_k: int = 12) -> list[tuple[int, float]]:
    return _index.search(query, top_k)


def rrf_merge(ranked_a: list[int], ranked_b: list[int], k: int = 60) -> list[int]:
    """두 랭킹의 Reciprocal Rank Fusion — id 순위 병합."""
    scores: dict[int, float] = {}
    for ranking in (ranked_a, ranked_b):
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda c: -scores[c])
