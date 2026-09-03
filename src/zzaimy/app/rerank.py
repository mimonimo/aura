"""리랭커 — 하이브리드 검색 상위 후보를 크로스인코더로 재정렬한다.

베이스라인 실측(docs/rerank-baseline.md): 표본 300건에서 한 번에 정답
0.490→0.623(+0.133), MRR 0.623→0.716(+0.093). GPU 상주 시 10쌍 약 0.2초로
대화 흐름에 부담 없음 (vLLM과 병행 실측 확인).

실패·미설치 환경에서는 원래 순서를 그대로 돌려준다 — 검색이 리랭커 때문에
죽는 일은 없어야 한다.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_MODEL = "BAAI/bge-reranker-v2-m3"
_ce = None
_failed = False


def _encoder():
    global _ce, _failed
    if _ce is not None or _failed:
        return _ce
    try:
        from sentence_transformers import CrossEncoder

        device = os.environ.get("ZZAIMY_RERANK_DEVICE", "cuda")
        try:
            _ce = CrossEncoder(_MODEL, device=device, max_length=512)
        except Exception:
            _ce = CrossEncoder(_MODEL, device="cpu", max_length=512)
        log.info("리랭커 적재 완료 (%s)", _MODEL)
    except Exception:
        log.warning("리랭커 사용 불가 — 하이브리드 순서 그대로 사용", exc_info=True)
        _failed = True
    return _ce


def rerank_chunks(query: str, chunks: list[dict], text_key: str = "content") -> list[dict]:
    """조각 목록을 질의 연관도 순으로 재정렬. 실패 시 입력 순서 유지."""
    if os.environ.get("ZZAIMY_NO_RERANK") or len(chunks) < 2:
        return chunks
    ce = _encoder()
    if ce is None:
        return chunks
    try:
        pairs = [
            (query, f"{c.get('heading', '')} {c.get(text_key, '')}"[:800])
            for c in chunks
        ]
        scores = ce.predict(pairs, show_progress_bar=False)
        order = sorted(range(len(chunks)), key=lambda i: -float(scores[i]))
        return [chunks[i] for i in order]
    except Exception:
        log.warning("리랭크 실패 — 원래 순서 유지", exc_info=True)
        return chunks
