"""그래프를 쓰는 근거 찾기 — 에이전트 리즈닝 루프의 앞 세 단계(ADR-0047 6항, ADR-0048).

1단계 질문 파악: 질문에서 사업(사업 카드의 이름·약칭), 연차(N차년도·연도), 문서 갈래(계획·실적·평가)를 찾는다.
2단계 그래프 탐색: 그 사업 → 연차 → 문서 → 절로 후보를 좁힌다(못 찾은 조건은 넓게 둔다).
3단계 근거 선택: 후보 절을 질문과의 낱말 겹침(제목 무게 2, 본문 1)으로 매겨 상위 k 를 사업→연차→문서→절 경로와 함께 낸다.
각 단계의 판단을 기록으로 돌려준다 — 리즈닝 학습 데이터의 꼴과 같다(질문 → 단계 판단 → 고른 근거).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from zzaimy.graph import kg_store

_ROUND = re.compile(r"([1-9])\s*차\s*년도")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:년|학년도)?")
_KIND_WORDS = {"plan": r"계획서|사업계획|계획(?!\s*대비)", "report": r"실적|결과\s*보고|성과\s*보고", "evaluation": r"평가\s*(?:결과|의견)|종합\s*의견|지적"}
_STOP = {"무엇", "어떻게", "있어", "있나", "알려", "줘", "대한", "관련", "사업", "내용", "해당", "그리고", "에서", "으로"}


_LEAD_ACRONYM = re.compile(r"^\s*([A-Z][A-Z0-9.]{2,})(?=[\s(]|$)")


def _surfaces(props: dict) -> set[str]:
    """사업을 부르는 표면형 — 이름·약칭에 더해 이름 앞의 영문 대문자 약칭(「AID (AI+Digital) …」의 AID)."""
    names = list(props.get("names") or []) + list(props.get("acronyms") or [])
    names += [m.group(1) for n in props.get("names") or [] if (m := _LEAD_ACRONYM.match(n))]
    return {re.sub(r"[\s.]+", "", s).upper() for s in names}


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", t or "") if w not in _STOP}


@dataclass
class Hit:
    section: str
    title: str
    path: list[str]                 # 사업 → 연차 → 문서 → 절 라벨
    score: float
    doc_id: int


@dataclass
class Trace:
    question: str
    program: str = ""
    program_label: str = ""
    round: int | None = None
    year: int | None = None
    kinds: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)


def retrieve(db, question: str, k: int = 5, chunk_text=None) -> Trace:
    """chunk_text(doc_id, seqs) → 본문(선택). seqs 가 None 이면 [(seq, 본문)] 전부를 돌려준다. 없으면 절 제목만으로 매긴다."""
    tr = Trace(question=question)
    q_flat = re.sub(r"[\s.]+", "", question or "").upper()
    progs = kg_store.nodes(db, "program")
    best = (0, None)
    for p in progs:
        surfaces = _surfaces(p["props"])
        hit = max((len(s) for s in surfaces if len(s) >= 3 and s in q_flat), default=0)
        if hit > best[0]:
            best = (hit, p)
    if best[1]:
        tr.program, tr.program_label = best[1]["id"], best[1]["label"]
        tr.steps.append(f"[1단계: 질문 파악] 사업 = {tr.program_label}({tr.program})")
    else:
        tr.steps.append("[1단계: 질문 파악] 사업 이름을 찾지 못함 — 모든 사업에서 찾는다")
    m = _ROUND.search(question or "")
    tr.round = int(m.group(1)) if m else None
    y = _YEAR.search(question or "")
    tr.year = int(y.group(1)) if y and not m else None
    tr.kinds = [kd for kd, pat in _KIND_WORDS.items() if re.search(pat, question or "")]
    tr.steps.append(f"연차 = {tr.round and f'{tr.round}차년도' or tr.year or '미지정'} · 문서 갈래 = {tr.kinds or '미지정'}")

    nodes = {n["id"]: n for n in kg_store.nodes(db)}
    edges = kg_store.edges(db, "contains")
    children: dict[str, list[str]] = {}
    for e in edges:
        children.setdefault(e["src"], []).append(e["dst"])
    years = [c for c in children.get(tr.program, []) if c.startswith("year:")] if tr.program else \
        [c for p in progs for c in children.get(p["id"], []) if c.startswith("year:")]
    narrowed = years
    if tr.round:
        narrowed = [y_ for y_ in years if y_.endswith(f":r{tr.round}")]
    elif tr.year:
        narrowed = [y_ for y_ in years if y_.endswith(f":y{tr.year}") or (nodes.get(y_, {}).get("props", {}).get("year") == tr.year)]
    if (tr.round or tr.year) and not narrowed:
        tr.steps.append("그 연차의 문서가 그래프에 없음 — 모든 연차에서 찾는다(답할 때 연차가 다름을 밝힌다)")
    years = narrowed or years
    docs = [d for y_ in years for d in children.get(y_, []) if d.startswith("doc:")]
    if tr.program:
        docs += [d for d in children.get(tr.program, []) if d.startswith("doc:")]
    if tr.kinds:
        docs = [d for d in docs if nodes.get(d, {}).get("props", {}).get("kind") in tr.kinds] or docs
    tr.steps.append(f"[2단계: 그래프 탐색] 연차 {len(years)}개 → 문서 {len(docs)}개: "
                    + ", ".join(nodes[d]["label"][:30] for d in docs[:4]) + (" …" if len(docs) > 4 else ""))
    year_of = {d: y_ for y_ in years for d in children.get(y_, [])}
    qw = _words(question)
    hits: list[Hit] = []
    for d in docs:
        if chunk_text and not any(":sec:" in c for c in children.get(d, [])):
            # 절 구조가 없는 문서(평가 종합의견 등)는 본문 조각을 그대로 후보로 둔다
            dn = nodes[d]
            for seq, text in chunk_text(dn["doc_id"], None) or []:
                sc = len(qw & _words(text)) / (len(qw) or 1)
                if sc > 0:
                    label = f"본문 {seq + 1}"
                    path = [tr.program_label or "", nodes.get(year_of.get(d, ""), {}).get("label", ""), dn["label"], label]
                    hits.append(Hit(section=f"{d}:chunk:{seq}", title=label, path=[p_ for p_ in path if p_],
                                    score=round(sc, 3), doc_id=int(dn["doc_id"] or 0)))
            continue
        stack = list(children.get(d, []))
        while stack:
            sid = stack.pop()
            stack += children.get(sid, [])
            sec = nodes.get(sid)
            if not sec or sec["type"] != "section":
                continue
            tw = _words(sec["label"])
            body = chunk_text(sec["doc_id"], sec["props"].get("chunks") or []) if chunk_text else ""
            bw = _words(body)
            sc = 2 * len(qw & tw) / (len(qw) or 1) + len(qw & bw) / (len(qw) or 1)
            if sc > 0:
                parent = nodes.get(sid.rsplit(".", 1)[0]) if "." in sid.split(":sec:")[1] else None
                path = [tr.program_label or "", nodes.get(year_of.get(d, ""), {}).get("label", ""), nodes[d]["label"],
                        (parent or {}).get("label", ""), sec["label"]]
                hits.append(Hit(section=sid, title=sec["label"], path=[p_ for p_ in path if p_], score=round(sc, 3),
                                doc_id=int(sec["doc_id"] or 0)))
    hits.sort(key=lambda h: -h.score)
    seen: set[tuple] = set()
    uniq = []
    for h in hits:                                   # 같은 문서·같은 경로(목차 줄과 본문 절 등)는 한 번만
        key = (h.doc_id, tuple(h.path[-2:]))
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    tr.hits = uniq[:k]
    tr.steps.append(f"[3단계: 근거 선택] 절 {len(hits)}개 중 상위 {len(tr.hits)}: "
                    + "; ".join(f"「{h.title[:30]}」({h.score})" for h in tr.hits[:3]))
    return tr
