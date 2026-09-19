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
# 쌍 길이 상한(토큰). 운영 VM(CPU 8코어) 실측 2026-09-14, 후보 10개 기준:
#   512 → 9.2s/질의, 256 → 4.6s/질의. 순위 일치 top-1 1.00 · 상위3 0.94 · 쌍별 0.96.
# 조각 본문 앞 350자 안팎(표제 + 조문 요지)이면 관련도 판단에 충분하다. int8 양자화는
# 2.9s까지 줄지만 쌍별 일치가 0.87로 떨어져 채택하지 않았다(리랭커의 일은 정밀한 순서).
_MAX_LEN = 256
_ce = None
_failed = False


def _encoder():
    global _ce, _failed
    if _ce is not None or _failed:
        return _ce
    try:
        # 캐시된 모델만 — 아웃바운드가 막힌 VM에서 허브 갱신 확인은 응답 없이 멈춘다
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        from sentence_transformers import CrossEncoder

        device = os.environ.get("ZZAIMY_RERANK_DEVICE", "cuda")
        try:
            _ce = CrossEncoder(_MODEL, device=device, max_length=_MAX_LEN)
        except Exception:
            _ce = CrossEncoder(_MODEL, device="cpu", max_length=_MAX_LEN)
        log.info("리랭커 적재 완료 (%s)", _MODEL)
    except Exception:
        log.warning("리랭커 사용 불가 — 하이브리드 순서 그대로 사용", exc_info=True)
        _failed = True
    return _ce


# 재랭킹 꼬리 자르기 — 1위 점수 대비 이 비율 아래는 근거로 올리지 않는다.
# 상대값으로 두는 이유: bge-reranker-v2-m3의 절대 점수는 모델 버전·max_length에
# 따라 눈금이 달라 임의의 숫자를 박을 수 없고, 이 저장소에서는 로컬에 모델이 없어
# 절대 분포를 실측하지 못했다(미확인). 반면 "1위보다 한참 못한 후보는 근거가 아니다"는
# 눈금과 무관하게 성립한다. 절대 하한이 필요하면 ZZAIMY_RERANK_MIN으로 넣는다
# (기본 0 = 사용 안 함) — 운영 VM에서 점수 분포를 재고 나서 채우는 자리다.
RERANK_TAIL_RATIO = 0.25


def rerank_scored(query: str, chunks: list[dict],
                  text_key: str = "content") -> list[tuple[dict, float]] | None:
    """(조각, 점수)를 점수 내림차순으로. 리랭커가 없거나 실패하면 None."""
    if os.environ.get("ZZAIMY_NO_RERANK") or not chunks:
        return None
    ce = _encoder()
    if ce is None:
        return None
    try:
        pairs = [
            (query, f"{c.get('heading', '')} {c.get(text_key, '')}"[:800])
            for c in chunks
        ]
        scores = [float(s) for s in ce.predict(pairs, show_progress_bar=False)]
        order = sorted(range(len(chunks)), key=lambda i: -scores[i])
        return [(chunks[i], scores[i]) for i in order]
    except Exception:
        log.warning("리랭크 실패 — 원래 순서 유지", exc_info=True)
        return None


def prune_scored(scored: list[tuple[dict, float]]) -> tuple[list[dict], bool]:
    """(남길 조각, 근거가 약한가) — 하한을 넘은 것만. 하나도 없으면 1위만 남기고 약함 표시.

    조용히 0건으로 만들지 않는 이유: 담당자에게는 "약한 근거라도 하나"가 "아무것도
    없음"보다 낫다. 대신 약하다는 사실을 데이터로 남겨 화면·답변이 밝힐 수 있게 한다.
    """
    if not scored:
        return [], False
    top = scored[0][1]
    abs_min = float(os.environ.get("ZZAIMY_RERANK_MIN", "0") or 0)
    ratio = float(os.environ.get("ZZAIMY_RERANK_TAIL_RATIO", RERANK_TAIL_RATIO))
    cut = top * ratio if top > 0 else float("-inf")
    kept = [c for c, s in scored if s >= max(cut, abs_min)]
    if kept:
        return kept, bool(abs_min and top < abs_min)
    return [scored[0][0]], True


def rerank_chunks(query: str, chunks: list[dict], text_key: str = "content",
                  prune: bool = False) -> list[dict]:
    """조각 목록을 질의 연관도 순으로 재정렬한다(기본은 재정렬만).

    prune=True면 1위 대비 RERANK_TAIL_RATIO 미만인 후보와 절대 하한
    (ZZAIMY_RERANK_MIN) 미달 후보를 뺀다 — 근거로 올릴 목록을 고를 때 쓴다.
    재정렬과 하한을 나눠 둔 이유: 평가 하네스는 순위 전체가 필요하고(자르면
    Recall@k를 못 잰다), 운영 검색은 못 미더운 후보를 빼야 한다.
    실패·미설치 환경에서는 입력 순서를 그대로 돌려준다.
    """
    if len(chunks) < 2:
        return chunks
    scored = rerank_scored(query, chunks, text_key)
    if scored is None:
        return chunks
    ordered = [c for c, _ in scored]
    if not prune:
        return ordered
    top = scored[0][1]
    abs_min = float(os.environ.get("ZZAIMY_RERANK_MIN", "0") or 0)
    ratio = float(os.environ.get("ZZAIMY_RERANK_TAIL_RATIO", RERANK_TAIL_RATIO))
    # 점수 눈금이 음수까지 갈 수 있어(로짓) 비율은 양수 구간에서만 뜻이 있다
    if abs_min and top < abs_min:
        return []                 # 1위조차 하한 미달 = 근거 없음
    cut = top * ratio if top > 0 else float("-inf")
    kept = [c for c, s in scored if s >= max(cut, abs_min)]
    return kept or ordered[:1]
