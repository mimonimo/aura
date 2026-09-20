"""LLM 리랭커 — 서빙 장비(토르)의 생성 모델로 검색 후보를 다시 매긴다.

왜: 크로스인코더(bge-reranker)는 VM CPU 에서 질의당 수 초가 걸리고, 교내 규정처럼
문맥 판단이 필요한 질의에서 한계가 있다. 토르에 올린 생성 모델에 "이 조각이 질의의
근거가 되는가"를 0~3점으로 묻고 그 점수로 정렬한다. 점수가 같으면 원래(하이브리드)
순서를 지킨다 — 모델이 헷갈릴 때 검색 순위를 망가뜨리지 않게.

켜는 법: 환경변수 ZZAIMY_LLM_RERANK=<OpenAI 호환 주소>|<모델>. 기본은 꺼져 있다.
평가: scripts/90_llm_rerank_eval.py (같은 질의 세트로 하이브리드·크로스인코더와 비교).
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor

PROMPT = (
    "당신은 대학 행정 문서 검색의 채점자다. 질의에 답하는 근거로 이 조각이 얼마나 쓸모 있는지 "
    "0~3 중 숫자 하나로만 답하라.\n"
    "3: 질의에 직접 답한다  2: 관련 조항이지만 일부만 답한다  1: 주제만 비슷하다  0: 무관하다\n\n"
    "질의: {q}\n\n조각 출처: {title} / {heading}\n조각:\n{body}\n\n점수:"
)
_DIGIT = re.compile(r"[0-3]")


def configured() -> tuple[str, str] | None:
    """(주소, 모델) — 환경변수로 켰을 때만."""
    raw = os.environ.get("ZZAIMY_LLM_RERANK", "").strip()
    if "|" not in raw:
        return None
    url, model = raw.split("|", 1)
    return (url.strip(), model.strip()) if url.strip() and model.strip() else None


def make_scorer(base_url: str, model: str, *, workers: int = 4, body_chars: int = 900):
    """score(질의, 후보 조각 목록) → 조각별 점수(0~3, 실패는 -1)."""
    from zzaimy.generate import client as _cl

    if _cl.OpenAI is None:
        raise RuntimeError("openai 패키지가 없습니다 — LLM 리랭커에 필요합니다")
    client = _cl.OpenAI(base_url=base_url, api_key="none", timeout=60, max_retries=1)
    extra = ({"reasoning_effort": "none"} if _cl.is_ollama(base_url)
             else {"chat_template_kwargs": {"enable_thinking": False}})

    def one(q: str, c: dict) -> int:
        try:
            r = client.chat.completions.create(
                model=model, temperature=0.0, max_tokens=4, extra_body=extra,
                messages=[{"role": "user", "content": PROMPT.format(
                    q=q, title=c.get("reg_title") or "", heading=c.get("heading") or "",
                    body=(c.get("content") or "")[:body_chars])}],
            )
            m = _DIGIT.search(r.choices[0].message.content or "")
            return int(m.group()) if m else 0
        except Exception:
            return -1          # 실패한 후보는 점수를 모른다

    def score(q: str, cands: list[dict]) -> list[int]:
        if not cands:
            return []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda c: one(q, c), cands))

    return score


def make_reranker(base_url: str, model: str, *, workers: int = 4, body_chars: int = 900):
    """rerank(질의, 후보 조각 목록) → 재정렬된 조각 목록. 동점은 원래 순서, 전부 실패하면 그대로."""
    score = make_scorer(base_url, model, workers=workers, body_chars=body_chars)

    def rerank(q: str, cands: list[dict]) -> list[dict]:
        if not cands:
            return cands
        scores = score(q, cands)
        if all(s < 0 for s in scores):
            return cands      # 모델이 전부 실패하면 검색 순서를 그대로 둔다
        order = sorted(range(len(cands)), key=lambda i: (-max(scores[i], 0), i))
        return [cands[i] for i in order]

    return rerank


_scorer_cache: dict = {}


def configured_scores(query: str, chunks: list[dict]) -> list[float] | None:
    """운영 검색용 — ZZAIMY_LLM_RERANK 가 켜져 있으면 0~1 점수(3점=1.0). 꺼져 있거나 전부 실패하면 None."""
    conf = configured()
    if conf is None:
        return None
    if conf not in _scorer_cache:
        _scorer_cache[conf] = make_scorer(*conf)
    raw = _scorer_cache[conf](query, chunks)
    if not raw or all(s < 0 for s in raw):
        return None
    return [max(s, 0) / 3.0 for s in raw]
