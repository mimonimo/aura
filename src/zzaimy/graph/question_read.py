"""질문을 온톨로지 말로 읽기 — 27B 가 질문을 사업·연도·연차·문서 갈래·따라갈 관계로 옮기고, 코드는 그대로 그래프를 탄다.

검색 앞 단계(graph/retrieve.scope)의 「질문 파악」 을 모델이 맡는다. 이름 맞추기·「N차년도」 정규식만으로는
「계획 대비 실적」 「앞 단계 사업」 처럼 관계를 묻는 질문을 알아보지 못한다. 모델은 판단만 하고(어느 사업·어느 관계),
관계를 실제로 따라가 문서를 고르는 일은 코드가 한다(에이전트가 판단, 코드는 도구).

모델에게 주는 것: 온톨로지의 관계 목록(아래 RELATIONS — 그래프에 실제로 있는 관계 종류)과 그래프의 사업 이름 목록.
모델이 고른 사업 이름은 목록에 있는 것만 받는다. 읽기에 실패하면(서버 없음·시간 초과·형식 오류) None 을 돌려주고,
부른 쪽은 예전처럼 이름 맞추기로 읽는다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# 따라갈 수 있는 관계 — 그래프의 관계 종류(kg_edges.kind)와 그 관계를 묻는 질문의 뜻. 모델에게 그대로 보여 준다
RELATIONS: dict[str, tuple[str, str]] = {
    "plan_report": ("plans_reports", "계획서와 그 연차의 실적보고서를 짝으로 본다 — 계획 대비 실적, 계획한 것이 어떻게 됐는지, 목표와 달성"),
    "evaluates": ("evaluates", "평가 문서와 평가받은 문서를 함께 본다 — 평가 결과·지적 사항·평가 의견"),
    "predecessor": ("succeeded_by", "이 사업의 앞 단계(전신) 사업까지 본다 — 이전 단계, 전신, 앞 사업"),
    "successor": ("succeeded_by", "이 사업의 다음 단계(후속) 사업까지 본다 — 후속 사업, 다음 단계"),
    "integrated": ("integrated_into", "이 사업이 편입된 사업, 이 사업에 편입된 사업까지 본다 — 편입·통합"),
    "related": ("related", "문서 근거로 이어진 연관 사업까지 본다 — 연관 사업, 함께 추진한 사업"),
    "group": ("contains", "같은 사업 묶음(갈래)의 다른 사업까지 본다 — 같은 갈래의 사업들 비교"),
}
KINDS = {"plan": "계획서", "report": "실적·결과 보고서", "evaluation": "평가 결과·의견"}

SCHEMA = {
    "type": "object",
    "properties": {
        "programs": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "years": {"type": "array", "items": {"type": "integer"}, "maxItems": 4},
        "rounds": {"type": "array", "items": {"type": "integer"}, "maxItems": 4},
        "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}},
        "relations": {"type": "array", "items": {"type": "string", "enum": list(RELATIONS)}},
    },
    "required": ["programs", "years", "rounds", "kinds", "relations"],
}


@dataclass
class Reading:
    programs: list[str] = field(default_factory=list)      # 그래프 사업 노드 id
    years: list[int] = field(default_factory=list)
    rounds: list[int] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)          # 모델이 고른 이름(기록용)
    seconds: float = 0.0


def _catalog(programs: list[dict]) -> tuple[str, dict[str, str]]:
    """모델에게 보일 사업 목록과 이름 → 노드 id. 이름은 화면 이름 하나, 약칭은 괄호로 덧붙인다(이름으로만 고르게)."""
    lines, by_name = [], {}
    for p in programs:
        label = (p.get("label") or "").strip()
        if not label:
            continue
        acr = [a for a in (p.get("props") or {}).get("acronyms") or [] if a and a not in label][:3]
        lines.append(f"- {label}" + (f" (약칭: {', '.join(acr)})" if acr else ""))
        by_name[label.replace(" ", "")] = p["id"]
    return "\n".join(lines), by_name


_PROMPT = """질문을 사업 문서 그래프에서 찾을 조건으로 옮긴다. JSON 하나만 낸다.

사업 목록(그래프에 있는 사업 — programs 에는 이 목록의 이름을 그대로 적는다. 질문이 사업을 말하지 않으면 빈 배열):
{catalog}

따라갈 관계(질문이 관계를 물을 때만 고른다):
{relations}

문서 갈래(질문이 갈래를 말할 때만): {kinds}
years 는 질문에 적힌 연도(예: 2024년, 2024학년도), rounds 는 「N차년도」의 N. 없으면 빈 배열.

질문: {question}"""


_cache: dict[str, tuple[float, Reading | None]] = {}
_lock = threading.Lock()
CACHE_SEC = 600


def read(client, question: str, programs: list[dict], timeout: float | None = None) -> Reading | None:
    """client: generate.client.VllmClient(또는 같은 꼴). 같은 질문은 10분 동안 다시 묻지 않는다."""
    q = " ".join((question or "").split())[:600]
    if not q or client is None:
        return None
    with _lock:
        hit = _cache.get(q)
        if hit and time.time() - hit[0] < CACHE_SEC:
            return hit[1]
    catalog, by_name = _catalog(programs)
    prompt = _PROMPT.format(catalog=catalog, question=q,
                            relations="\n".join(f"- {k}: {v[1]}" for k, v in RELATIONS.items()),
                            kinds=", ".join(f"{k}({v})" for k, v in KINDS.items()))
    t0 = time.time()
    out: Reading | None = None
    try:
        api = client.client.with_options(timeout=timeout or float(os.environ.get("ZZAIMY_GRAPH_READ_TIMEOUT", "20")), max_retries=0)
        resp = api.chat.completions.create(
            model=client.model, messages=[{"role": "user", "content": prompt}], temperature=0.0, max_tokens=300,
            response_format={"type": "json_schema", "json_schema": {"name": "GraphQuery", "schema": SCHEMA}},
            extra_body=getattr(client, "_extra", None) or None)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else raw
        data = json.loads(raw)
        names = [str(n).strip() for n in data.get("programs") or [] if str(n).strip()]
        ids = list(dict.fromkeys(by_name[n.replace(" ", "")] for n in names if n.replace(" ", "") in by_name))
        out = Reading(programs=ids, names=names,
                      years=[int(y) for y in data.get("years") or [] if 1990 <= int(y) <= 2100][:4],
                      rounds=[int(r) for r in data.get("rounds") or [] if 1 <= int(r) <= 20][:4],
                      kinds=[k for k in data.get("kinds") or [] if k in KINDS],
                      relations=list(dict.fromkeys(r for r in data.get("relations") or [] if r in RELATIONS)),
                      seconds=round(time.time() - t0, 2))
    except Exception as e:                              # 읽기 실패는 이름 맞추기로 물러난다 — 검색은 멈추지 않는다
        log.warning("질문 읽기 실패(이름 맞추기로): %s: %s", type(e).__name__, str(e)[:200])
        out = None
    with _lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[q] = (time.time(), out)
    return out


def reader_for(role: str = "answer", force: bool = False):
    """검색에 넘길 읽기 함수 — question, programs → Reading | None. 서버 연결은 처음 쓸 때 만든다.
    운영 답변은 ZZAIMY_GRAPH_READ=1 일 때만 쓴다(채택은 scripts/184 대결 수치로). force 는 측정용."""
    if not force and os.environ.get("ZZAIMY_GRAPH_READ", "0") != "1":
        return None
    state: dict = {}

    def _read(question: str, programs: list[dict]) -> Reading | None:
        if "client" not in state:
            try:
                from zzaimy.generate.client import VllmClient
                state["client"] = VllmClient(role=role)
            except Exception as e:
                log.warning("질문 읽기 서버 없음: %s", e)
                state["client"] = None
        return read(state["client"], question, programs)
    return _read
