"""임베딩 검색 (KURE-v1) — 규정 조각 하이브리드 검색의 dense 축.

52_embed_chunks.py가 만든 사전 계산 벡터(npz)를 읽고, 질의만 실시간 임베딩한다.
모델·인덱스는 지연 로드하며, 준비물이 없으면 조용히 비활성(키위 검색만 동작).
P3에서 Qdrant로 이관하기 전까지의 인메모리 구현.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# 후보 모델을 운영 색인을 건드리지 않고 시험할 수 있게 환경변수로 바꿔 끼운다.
# 둘은 반드시 같은 모델로 맞춰야 한다 — 색인과 질의를 다른 모델로 만들면 벡터 공간이 어긋난다.
INDEX_PATH = Path(os.environ.get("ZZAIMY_EMBED_INDEX", "data/platform/chunk_embeddings.npz"))
MODEL_NAME = os.environ.get("ZZAIMY_EMBED_MODEL", "nlpai-lab/KURE-v1")

# 유사도 하한 — 코퍼스가 스스로 잡음 바닥을 재게 한다.
#
# KURE 공간에서 한국어 행정문서끼리의 코사인은 바닥이 높다. 실측(2026-09-19,
# corpus_pilot 1,846조각): 서로 다른 문서의 무작위 조각 쌍 19만 건이 평균 0.511 ·
# 표준편차 0.090이고, 같은 문서 안 조각 쌍은 평균 0.746이다. 0.5 근처는 "관련"이
# 아니라 "한국어 행정문서라서" 나오는 값이다.
#
# 그래서 절대 숫자를 박지 않고, 적재된 인덱스에서 무작위 쌍을 뽑아 평균·표준편차를
# 구한 뒤 평균 + k·표준편차를 하한으로 쓴다. 모델·코퍼스가 바뀌어도 따라간다.
# k별 실측 (무관 쌍 통과율 / 같은 문서 쌍 통과율):
#   0.75 → 0.579  18.7% / 93.3%      1.25 → 0.624   9.1% / 83.4%
#   1.00 → 0.601  13.1% / 89.1%      1.50 → 0.647   6.7% / 76.4%
# k=1.0을 기본값으로 둔다 — 잡음의 87%를 걷어 내면서 실제 관련 쌍의 89%를 지킨다.
# 환경변수 ZZAIMY_DENSE_FLOOR_K 로 조절한다(0 이하면 하한을 쓰지 않는다).
DENSE_FLOOR_K = 1.0
_FLOOR_SAMPLE_PAIRS = 20000


class EmbedIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: object | None = None
        self._ids: object = None
        self._vectors: object = None
        self._failed = False
        self._floor: float | None = None

    def _load(self) -> bool:
        if self._failed:
            return False
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                import os

                # 캐시된 모델만 쓴다. 운영 VM은 아웃바운드가 막혀 있어 허브 갱신
                # 확인을 시도하면 응답 없이 멈춘다(서비스 유닛엔 이미 설정돼 있지만
                # 스크립트·테스트가 같은 함정에 빠지지 않게 여기서도 기본값으로 둔다).
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

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

    def noise_floor(self) -> float | None:
        """이 인덱스의 '무관 기준선' — 무작위 조각 쌍 코사인의 평균 + k·표준편차.

        인덱스가 없으면 None. 한 번 계산해 두고 재사용한다(표본 2만 쌍, 수 ms).
        """
        if self._floor is not None:
            return self._floor
        k = float(os.environ.get("ZZAIMY_DENSE_FLOOR_K", DENSE_FLOOR_K))
        if k <= 0 or not self._load():
            return None
        try:
            import numpy as np

            v = self._vectors
            n = len(v)  # type: ignore[arg-type]
            if n < 50:            # 표본이 너무 적으면 기준선을 못 잡는다
                return None
            rng = np.random.default_rng(0)
            a = rng.integers(0, n, _FLOOR_SAMPLE_PAIRS)
            b = rng.integers(0, n, _FLOOR_SAMPLE_PAIRS)
            m = a != b
            sims = np.einsum("ij,ij->i", v[a[m]], v[b[m]])  # type: ignore[index]
            self._floor = float(sims.mean() + k * sims.std())
            log.info("임베딩 무관 기준선 %.3f (평균 %.3f · 표준편차 %.3f · k=%.2f)",
                     self._floor, float(sims.mean()), float(sims.std()), k)
        except Exception:
            return None
        return self._floor

    def search(self, query: str, top_k: int = 12,
               min_sim: float | None = None) -> list[tuple[int, float]]:
        """(chunk_id, 유사도) 상위 top_k. 비활성이면 빈 목록.

        min_sim 미만은 돌려주지 않는다 — 기본값은 인덱스에서 계산한 무관 기준선.
        근거가 없으면 빈 목록이 정답이다(억지로 채우지 않는다).
        """
        if not self._load():
            return []
        model = self._model
        ids, vectors = self._ids, self._vectors
        assert model is not None and ids is not None and vectors is not None
        qv = model.encode([query], normalize_embeddings=True)[0]  # type: ignore[attr-defined]
        sims = vectors @ qv  # type: ignore[operator]
        order = sims.argsort()[-top_k:][::-1]
        floor = self.noise_floor() if min_sim is None else min_sim
        out = [(int(ids[i]), float(sims[i])) for i in order]  # type: ignore[index]
        if floor is None:
            return out
        return [(cid, s) for cid, s in out if s >= floor]


_index = EmbedIndex()


def embed_search(query: str, top_k: int = 12,
                 min_sim: float | None = None) -> list[tuple[int, float]]:
    return _index.search(query, top_k, min_sim)


def dense_floor() -> float | None:
    """현재 인덱스의 무관 기준선 — 화면·점검용(없으면 None)."""
    return _index.noise_floor()


def embed_texts(texts: list[str]):
    """텍스트 목록을 정규화 벡터(ndarray)로 — 모델이 없으면 None.

    검색 외 용도(섹션별 재료 선별·배점 커버리지 의미 판정)에 같은 학습 모델을
    재사용한다. 키워드 겹침 같은 규칙 대신 의미 표현으로 판단하기 위한 공용 입구.
    호출부는 None이면 규칙 기반 기존 동작으로 조용히 내려간다.
    """
    if not texts or not _index._load():
        return None
    model = _index._model
    return model.encode(list(texts), normalize_embeddings=True)  # type: ignore[attr-defined]


def rrf_merge(
    ranked_a: list[int], ranked_b: list[int], k: int = 60,
    w_a: float = 1.0, w_b: float = 1.0,
) -> list[int]:
    """두 랭킹의 가중 Reciprocal Rank Fusion — id 순위 병합.

    미니 베이스라인 실측(2026-09-01)에서 임베딩 단독(MRR .808)이 동가중
    하이브리드(.780)보다 나아, 기본 가중은 호출부에서 임베딩 우세로 준다.
    """
    scores: dict[int, float] = {}
    for ranking, w in ((ranked_a, w_a), (ranked_b, w_b)):
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank + 1)
    return sorted(scores, key=lambda c: -scores[c])
