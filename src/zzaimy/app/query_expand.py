"""질의 확장 — 상황으로 묻는 질문을 규정 용어 검색어로 풀어 검색에 덧붙인다.

왜: 담당자는 규정 원문 용어를 모른 채 '계약직 뽑으면 누가 최종 결재해?'처럼 묻는다. 어휘 검색은
'임용권자'를 못 찾고, 임베딩도 상황 서술과 조문 사이 거리가 멀다(바꿔 말한 질의 측정: 상위 3 적중
12건 중 6건). 서빙 장비의 작은 모델로 규정에 나올 법한 용어를 몇 개 뽑아 원 질의 뒤에 붙인다.
모델이 지어낸 낱말은 검색어일 뿐 답변 근거가 아니므로 절대규칙 1과 부딪히지 않는다.

켜는 법: ZZAIMY_QUERY_EXPAND=answer — 답변 연결(용도 answer)의 모델을 그대로 쓴다(모델을 바꾸면 따라 바뀐다, 권장).
<OpenAI 호환 주소>|<모델> 로 따로 정할 수도 있다(그 모델에 묶인다). 기본은 꺼져 있다. 평가: scripts/90 --expand.
"""
from __future__ import annotations

import os
import re

PROMPT = (
    "다음은 대학 행정 담당자의 질문이다. 이 질문에 답하는 교내 규정·공고 조항을 찾기 위한 검색어를 "
    "규정 문서에 쓰일 법한 용어로 3~6개 뽑아라. 쉼표로만 구분해 한 줄로 출력한다. 설명 금지.\n\n질문: {q}"
)


ROLE_FORMS = ("answer", "role:answer")


def configured() -> tuple[str, str] | str | None:
    """("주소", "모델") · "answer"(답변 연결을 따름) · None(꺼짐)."""
    raw = os.environ.get("ZZAIMY_QUERY_EXPAND", "").strip()
    if raw.lower() in ROLE_FORMS:
        return "answer"
    if "|" not in raw:
        return None
    url, model = raw.split("|", 1)
    return (url.strip(), model.strip()) if url.strip() and model.strip() else None


_clients: dict = {}


def expand(query: str, base_url: str | None = None, model: str | None = None) -> str:
    """원 질의 + 확장 검색어. 설정이 없거나 실패하면 원 질의 그대로."""
    conf = (base_url, model) if base_url and model else configured()
    if conf is None or not (query or "").strip():
        return query
    try:
        from zzaimy.generate import client as _cl

        # 생성 호출은 VllmClient 를 거친다 — 생각 끄기 인자(Ollama·vLLM·연결 설정)는 거기서 정한다.
        # 후보 모델 점검(ZZAIMY_LLM_TRY_*) 중이면 확장도 후보 모델로 돈다(scripts/195)
        trial = bool(os.environ.get("ZZAIMY_LLM_TRY_CONN") or os.environ.get("ZZAIMY_LLM_TRY_MODEL"))
        by_role = trial or conf == "answer"
        key = ("answer",) if by_role else conf
        if key not in _clients:
            _clients[key] = (_cl.VllmClient(role="answer", timeout=20, max_retries=0) if by_role
                             else _cl.VllmClient(base_url=conf[0], model=conf[1], timeout=20, max_retries=0))
        c = _clients[key]
        r = c.client.chat.completions.create(
            model=c.model, temperature=0.0, max_tokens=60, extra_body=c._extra,
            messages=[{"role": "user", "content": PROMPT.format(q=query[:500])}])
        terms = [t.strip(" .·\"'") for t in re.split(r"[,，\n]", r.choices[0].message.content or "")]
        terms = [t for t in terms if 1 < len(t) <= 20][:6]
        return f"{query} {' '.join(terms)}" if terms else query
    except Exception:
        return query
