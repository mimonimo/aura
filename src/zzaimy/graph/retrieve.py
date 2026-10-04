"""그래프를 쓰는 근거 찾기 — 에이전트 리즈닝 루프의 앞 세 단계(ADR-0047 6항, ADR-0048).

1단계 질문 파악: 질문에서 사업(사업 카드의 이름·약칭), 연차(N차년도·연도), 문서 갈래(계획·실적·평가)를 찾는다.
2단계 그래프 탐색: 그 사업 → 연차 → 문서 → 절로 후보를 좁힌다. 명시한 조건에 맞는 문서가 없으면 근거 없음으로 반환한다.
3단계 근거 선택: 후보 절을 질문과의 낱말 겹침(제목 무게 2, 본문 1)으로 매겨 상위 k 를 사업→연차→문서→절 경로와 함께 낸다.
각 단계의 판단을 기록으로 돌려준다 — 리즈닝 학습 데이터의 꼴과 같다(질문 → 단계 판단 → 고른 근거).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from zzaimy.graph import kg_store

_ROUND = re.compile(r"(?<!\d)([1-9]\d*)\s*차\s*년도")
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
    tr.year = int(y.group(1)) if y else None
    tr.kinds = [kd for kd, pat in _KIND_WORDS.items() if re.search(pat, question or "")]
    scope = " · ".join(str(v) for v in (tr.year, f"{tr.round}차년도" if tr.round else None) if v)
    tr.steps.append(f"연차 = {scope or '미지정'} · 문서 갈래 = {tr.kinds or '미지정'}")

    nodes = {n["id"]: n for n in kg_store.nodes(db)}
    edges = kg_store.edges(db, "contains")
    children: dict[str, list[str]] = {}
    for e in edges:
        children.setdefault(e["src"], []).append(e["dst"])
    years = [c for c in children.get(tr.program, []) if c.startswith("year:")] if tr.program else \
        [c for p in progs for c in children.get(p["id"], []) if c.startswith("year:")]
    narrowed = years
    if tr.round:
        narrowed = [y_ for y_ in narrowed if y_.endswith(f":r{tr.round}")
                    or nodes.get(y_, {}).get("props", {}).get("round") == tr.round]
    if tr.year:
        narrowed = [y_ for y_ in narrowed if y_.endswith(f":y{tr.year}") or (nodes.get(y_, {}).get("props", {}).get("year") == tr.year)]
    if (tr.round or tr.year) and not narrowed:
        tr.steps.append("[2단계: 그래프 탐색] 요청한 연차의 문서가 없음 — 대상 연차 확인 또는 추가 자료 필요")
        return tr
    years = narrowed
    docs = [d for y_ in years for d in children.get(y_, []) if d.startswith("doc:")]
    if tr.program and tr.round is None and tr.year is None:
        docs += [d for d in children.get(tr.program, []) if d.startswith("doc:")]
    if tr.kinds:
        docs = [d for d in docs if nodes.get(d, {}).get("props", {}).get("kind") in tr.kinds]
        if not docs:
            tr.steps.append("[2단계: 그래프 탐색] 요청한 문서 갈래의 근거가 없음 — 문서 갈래 확인 또는 추가 자료 필요")
            return tr
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


_PROG_CACHE: dict = {"at": 0.0, "rows": None}


def _programs(db) -> list[dict]:
    """사업 노드(백여 개) — 5분 캐시. 질의마다 그래프 전체를 읽지 않는다."""
    import json
    import time
    if _PROG_CACHE["rows"] is None or time.time() - _PROG_CACHE["at"] > 300:
        with db._conn() as conn:
            rows = conn.execute("SELECT id, label, props FROM kg_nodes WHERE type = 'program'").fetchall()
        _PROG_CACHE.update(at=time.time(), rows=[{"id": r[0], "label": r[1], "props": json.loads(r[2] or "{}")} for r in rows])
    return _PROG_CACHE["rows"]


@dataclass
class Scope:
    program: str = ""
    program_label: str = ""
    round: int | None = None
    year: int | None = None
    kinds: list[str] = field(default_factory=list)
    docs: set[int] | None = None                   # 사업·연차·갈래로 좁힌 문서 번호(사업을 못 찾으면 None)
    program_docs: set[int] | None = None           # 사업 전체 문서(좁힌 범위가 비거나 맞는 조각이 없을 때 물러날 곳)
    path_of: dict[int, list[str]] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)


def scope(db, question: str) -> Scope:
    """retrieve 의 1·2단계만 — 사업·연차·문서 갈래로 문서 범위를 SQL 몇 번으로 정한다(검색 질의마다 쓰는 가벼운 길, 10/5:
    그래프 전체를 읽던 retrieve 가 질의 하나에 19초)."""
    import json
    sc = Scope()
    q_flat = re.sub(r"[\s.]+", "", question or "").upper()
    best = (0, None)
    for p in _programs(db):
        # 문서에서 모은 이름·약칭에 더해 화면에 보이는 정식 이름(장부 name)과 괄호를 뗀 꼴도 — 사용자는 보이는 이름으로 묻는다
        label = re.sub(r"[\s.]+", "", p["label"] or "").upper()
        surf = _surfaces(p["props"]) | {label, re.sub(r"\([^)]*\)", "", label)}
        hit = max((len(s) for s in surf if len(s) >= 3 and s in q_flat), default=0)
        if hit > best[0]:
            best = (hit, p)
    m = _ROUND.search(question or "")
    sc.round = int(m.group(1)) if m else None
    y = _YEAR.search(question or "")
    sc.year = int(y.group(1)) if y else None
    sc.kinds = [kd for kd, pat in _KIND_WORDS.items() if re.search(pat, question or "")]
    if not best[1]:
        sc.steps.append("[1단계: 질문 파악] 사업 이름을 찾지 못함 — 모든 사업에서 찾는다")
        return sc
    sc.program, sc.program_label = best[1]["id"], best[1]["label"]
    sc.steps.append(f"[1단계: 질문 파악] 사업 = {sc.program_label}({sc.program})")
    when = " · ".join(str(v) for v in (sc.year, f"{sc.round}차년도" if sc.round else None) if v)
    sc.steps.append(f"연차 = {when or '미지정'} · 문서 갈래 = {sc.kinds or '미지정'}")
    with db._conn() as conn:
        years = {r[0]: (r[1], json.loads(r[2] or "{}")) for r in conn.execute(
            "SELECT n.id, n.label, n.props FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst"
            " WHERE e.kind = 'contains' AND e.src = ? AND e.dst LIKE 'year:%'", (sc.program,)).fetchall()}
        srcs = [sc.program, *years]
        rows = conn.execute(
            "SELECT e.src, n.doc_id, n.label, n.props FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst WHERE e.kind = 'contains'"
            f" AND e.src IN ({','.join('?' * len(srcs))}) AND e.dst LIKE 'doc:%' AND n.doc_id IS NOT NULL", srcs).fetchall()
    sc.program_docs = set()
    picked = set()
    for src, did, label, props in rows:
        did = int(did)
        sc.program_docs.add(did)
        ylabel, yprops = years.get(src, ("", {}))
        sc.path_of.setdefault(did, [x for x in (sc.program_label, ylabel) if x])
        if sc.round and not (src.endswith(f":r{sc.round}") or yprops.get("round") == sc.round):
            continue
        if sc.year and not (src.endswith(f":y{sc.year}") or yprops.get("year") == sc.year):
            continue
        if (sc.round or sc.year) and src == sc.program:
            continue                              # 연차를 모르는 문서는 연차를 물은 질문의 범위가 아니다
        if sc.kinds and json.loads(props or "{}").get("kind") not in sc.kinds:
            continue
        picked.add(did)
    sc.docs = picked or sc.program_docs
    sc.steps.append(f"[2단계: 그래프 탐색] 사업 문서 {len(sc.program_docs)}건 → 연차·갈래로 {len(picked)}건"
                    + ("" if picked else " (맞는 문서가 없어 사업 전체)"))
    return sc
