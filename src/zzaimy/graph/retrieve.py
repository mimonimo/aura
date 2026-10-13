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
    programs: list[str] = field(default_factory=list)       # 질문이 말한 사업(여럿일 수 있다)
    relations: list[str] = field(default_factory=list)      # 따라간 관계(question_read.RELATIONS 의 열쇠)
    cover: list[tuple[str, set[int]]] = field(default_factory=list)   # 근거에 한 조각씩은 꼭 들어갈 묶음(관계의 양쪽)
    partners: dict[int, list[tuple[str, int]]] = field(default_factory=dict)  # 문서 → [(관계, 짝 문서)] — 근거에 든 문서의 짝을 담는다
    read_by: str = "이름 맞추기"


def _flat(t: str) -> str:
    return re.sub(r"[\s.]+", "", t or "").upper()


def _match_program(question: str, progs: list[dict]) -> dict | None:
    """이름 맞추기 — 화면 이름(장부 name)이 질문에 있으면 먼저, 그다음 문서에서 모은 이름·약칭. 모델 읽기가 없을 때 쓴다."""
    q_flat = _flat(question)
    best = ((0, 0), None)
    for p in progs:
        # 화면에 보이는 정식 이름이 질문에 들어 있으면 그것이 먼저다. 다른 카드에 섞인 같은 길이 이름과 비기면
        # 먼저 나온 카드가 이겨 비슷한 이름의 다른 사업으로 간다. 그다음 문서에서 모은 이름·약칭
        label = _flat(p["label"])
        lab_hit = max((len(s) for s in (label, re.sub(r"\([^)]*\)", "", label)) if len(s) >= 4 and s in q_flat), default=0)
        hit = max((len(s) for s in _surfaces(p["props"]) if len(s) >= 3 and s in q_flat), default=0)
        key = (lab_hit, hit)
        if key > best[0]:
            best = (key, p)
    return best[1]


def _related_programs(conn, ids: list[str], relations: list[str]) -> dict[str, str]:
    """관계를 따라 더 볼 사업 — {사업 id: 어떤 관계로 왔는지}. 그래프에 있는 관계만 탄다."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    out: dict[str, str] = {}

    def add(rows, why):
        for (pid,) in rows:
            if pid not in ids and pid.startswith("program:"):
                out.setdefault(pid, why)
    if "predecessor" in relations:
        add(conn.execute(f"SELECT src FROM kg_edges WHERE kind = 'succeeded_by' AND dst IN ({ph})", ids), "앞 단계 사업")
    if "successor" in relations:
        add(conn.execute(f"SELECT dst FROM kg_edges WHERE kind = 'succeeded_by' AND src IN ({ph})", ids), "다음 단계 사업")
    if "integrated" in relations:
        add(conn.execute(f"SELECT dst FROM kg_edges WHERE kind = 'integrated_into' AND src IN ({ph})", ids), "편입된 사업")
        add(conn.execute(f"SELECT src FROM kg_edges WHERE kind = 'integrated_into' AND dst IN ({ph})", ids), "편입해 온 사업")
    if "related" in relations:
        add(conn.execute(f"SELECT dst FROM kg_edges WHERE kind = 'related' AND src IN ({ph})", ids), "연관 사업")
        add(conn.execute(f"SELECT src FROM kg_edges WHERE kind = 'related' AND dst IN ({ph})", ids), "연관 사업")
    if "group" in relations:
        add(conn.execute(f"SELECT b.dst FROM kg_edges a JOIN kg_edges b ON b.src = a.src AND b.kind = 'contains'"
                         f" WHERE a.kind = 'contains' AND a.src LIKE 'group:%' AND a.dst IN ({ph})", ids), "같은 사업 묶음")
    return out


def _doc_pairs(conn, kind: str, doc_ids: set[int]) -> list[tuple[int, int]]:
    """문서끼리의 관계(계획→실적, 평가함→평가받음) 가운데 한쪽이 doc_ids 에 든 것 — (src 문서, dst 문서)."""
    if not doc_ids:
        return []
    nodes = [f"doc:{d}" for d in doc_ids]
    out = []
    for i in range(0, len(nodes), 500):
        part = nodes[i:i + 500]
        ph = ",".join("?" * len(part))
        for a, b in conn.execute(f"SELECT src, dst FROM kg_edges WHERE kind = ? AND (src IN ({ph}) OR dst IN ({ph}))",
                                 [kind, *part, *part]):
            if ":sec:" in a or ":sec:" in b:
                continue
            try:
                out.append((int(a.split(":")[1]), int(b.split(":")[1])))
            except (IndexError, ValueError):
                continue
    return out


def scope(db, question: str, reader=None) -> Scope:
    """retrieve 의 1·2단계 — 사업·연차·문서 갈래로 문서 범위를 SQL 몇 번으로 정하고, 질문이 관계를 물으면 그 관계를 따라 넓힌다.
    reader(question, programs) → question_read.Reading: 질문을 온톨로지 말로 읽는 모델(없거나 실패하면 이름 맞추기).
    검색 질의마다 쓰는 가벼운 길이다(그래프 전체를 읽는 retrieve 는 측정 결과 질의 하나에 19초가 걸렸다)."""
    import json
    sc = Scope()
    progs = _programs(db)
    by_id = {p["id"]: p for p in progs}
    reading = None
    if reader is not None:
        try:
            reading = reader(question, progs)
        except Exception:
            reading = None
    # 이름 맞추기 — 질문에 적힌 사업 이름·「N차년도」·갈래 낱말. 적힌 것은 이것이 먼저다(측정: 모델만 쓰면 보통 질문이 나빠졌다)
    hit = _match_program(question, progs)
    sc.programs = [hit["id"]] if hit else []
    m = _ROUND.search(question or "")
    rounds = [int(m.group(1))] if m else []
    y = _YEAR.search(question or "")
    years = [int(y.group(1))] if y else []
    sc.kinds = [kd for kd, pat in _KIND_WORDS.items() if re.search(pat, question or "")]
    if reading is not None:
        # 모델 읽기는 이름 맞추기가 못 하는 것만 보탠다 — 따라갈 관계, 둘째 사업, 적히지 않은(바꿔 말한) 사업·연도·갈래
        sc.read_by = "이름 맞추기 + 모델 읽기"
        sc.relations = list(reading.relations)
        sc.programs = list(dict.fromkeys([*sc.programs, *(pid for pid in reading.programs if pid in by_id)]))
        years = years or list(reading.years)
        rounds = rounds or list(reading.rounds)
        sc.kinds = sc.kinds or list(reading.kinds)
    sc.round = rounds[0] if rounds else None
    sc.year = years[0] if years else None
    if not sc.programs:
        sc.steps.append(f"[1단계: 질문 파악 · {sc.read_by}] 사업 이름을 찾지 못함 — 모든 사업에서 찾는다"
                        + (f" (관계 {', '.join(sc.relations)} 는 기준 사업이 없어 따라가지 않음)" if sc.relations else ""))
        return sc
    sc.program = sc.programs[0]
    sc.program_label = by_id[sc.program]["label"]
    labels = " · ".join(by_id[p]["label"] for p in sc.programs)
    sc.steps.append(f"[1단계: 질문 파악 · {sc.read_by}] 사업 = {labels}")
    when = " · ".join([*(str(y_) for y_ in years), *(f"{r}차년도" for r in rounds)])
    sc.steps.append(f"연차 = {when or '미지정'} · 문서 갈래 = {sc.kinds or '미지정'}"
                    + (f" · 관계 = {', '.join(sc.relations)}" if sc.relations else ""))
    with db._conn() as conn:
        extra = _related_programs(conn, sc.programs, sc.relations)
        all_progs = [*sc.programs, *(p for p in extra if p in by_id)]
        years_of: dict[str, tuple[str, dict]] = {}
        owner: dict[str, str] = {}
        for pid in all_progs:
            for nid, label, props in conn.execute(
                    "SELECT n.id, n.label, n.props FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst"
                    " WHERE e.kind = 'contains' AND e.src = ? AND e.dst LIKE 'year:%'", (pid,)).fetchall():
                years_of[nid] = (label, json.loads(props or "{}"))
                owner[nid] = pid
        srcs = [*all_progs, *years_of]
        rows = []
        for i in range(0, len(srcs), 500):
            part = srcs[i:i + 500]
            rows += conn.execute(
                "SELECT e.src, n.doc_id, n.label, n.props FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst WHERE e.kind = 'contains'"
                f" AND e.src IN ({','.join('?' * len(part))}) AND e.dst LIKE 'doc:%' AND n.doc_id IS NOT NULL", part).fetchall()
        sc.program_docs = set()
        picked: set[int] = set()
        added: dict[str, set[int]] = {}
        kind_of: dict[int, str] = {}
        for src, did, label, props in rows:
            did = int(did)
            pid = owner.get(src, src)
            ylabel, yprops = years_of.get(src, ("", {}))
            sc.path_of.setdefault(did, [x for x in (by_id.get(pid, {}).get("label", ""), ylabel) if x])
            kind_of[did] = json.loads(props or "{}").get("kind") or ""
            if pid in extra:                       # 관계로 온 사업 — 연차는 그 사업 것이 아니므로 갈래만 본다
                if not sc.kinds or kind_of[did] in sc.kinds:
                    added.setdefault(extra[pid], set()).add(did)
                continue
            sc.program_docs.add(did)
            if rounds and not any(src.endswith(f":r{r}") or yprops.get("round") == r for r in rounds):
                continue
            if years and not any(src.endswith(f":y{y_}") or yprops.get("year") == y_ for y_ in years):
                continue
            if (rounds or years) and src == pid:
                continue                              # 연차를 모르는 문서는 연차를 물은 질문의 범위가 아니다
            if sc.kinds and kind_of[did] not in sc.kinds:
                continue                              # 짝(계획↔실적·평가)은 아래에서 관계로 더한다 — 갈래는 출발 문서에만
            picked.add(did)
        base = picked or set(sc.program_docs)
        sc.steps.append(f"[2단계: 그래프 탐색] 사업 문서 {len(sc.program_docs)}건 → 연차·갈래로 {len(picked)}건"
                        + ("" if picked else " (맞는 문서가 없어 사업 전체)"))
        docs = set(base)
        for why, ds in added.items():
            docs |= ds
            sc.cover.append((why, ds))
            sc.steps.append(f"[관계] {why} {len(ds)}건을 더 본다: "
                            + ", ".join(by_id[p]["label"] for p, w in extra.items() if w == why and p in by_id)[:80])
        if "plan_report" in sc.relations:
            pairs = _doc_pairs(conn, "plans_reports", base)
            if pairs:
                plans, reports = {a for a, _ in pairs}, {b for _, b in pairs}
                docs |= plans | reports
                for a, b in pairs:
                    sc.partners.setdefault(a, []).append(("그 계획서의 실적보고서", b))
                    sc.partners.setdefault(b, []).append(("그 실적보고서의 계획서", a))
                sc.cover += [("계획서", plans), ("실적보고서", reports)]
                sc.steps.append(f"[관계] 계획↔실적 짝 {len(pairs)}쌍 — 계획서 {len(plans)}건·실적보고서 {len(reports)}건을 함께 본다")
            else:
                sc.steps.append("[관계] 계획↔실적 짝을 그래프에서 찾지 못함 — 좁힌 문서에서만 찾는다")
        if "evaluates" in sc.relations:
            pairs = _doc_pairs(conn, "evaluates", base)
            if pairs:
                judges = {a for a, _ in pairs}
                docs |= judges | {b for _, b in pairs}
                for a, b in pairs:
                    sc.partners.setdefault(b, []).append(("그 문서의 평가", a))
                sc.cover.append(("평가 문서", judges))
                sc.steps.append(f"[관계] 평가 관계 {len(pairs)}건 — 평가 문서 {len(judges)}건을 함께 본다")
    sc.docs = docs
    return sc
