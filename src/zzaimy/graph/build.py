"""지식 그래프 — 구조 관계 + 사업 정체 기반 연관 (ADR-0009 / 온톨로지 v2).

모든 간선에는 '왜 이어졌는지'가 붙는다(reason·why·evidence). 근거를 댈 수 없는
간선은 만들지 않는다. 낱말이 겹친다는 이유로 문서를 잇던 방식은 폐기했다 —
실측(corpus_pilot 133문서)에서 그 방식의 간선 774개는 전부 '평가위원회·한국연구재단
같은 역할 명칭을 함께 언급했다'는 이유뿐이었고, 정작 같은 사업의 공고·기본계획·서식은
서로 이어지지 않았다.

온톨로지
  노드: criteria(기준 문서) · intake(접수 문서) · project(프로젝트)
       · entity(사업·기관·법령 — 연결 근거가 되는 것만)
  간선: refers(접수→기준 근거) · belongs(문서→프로젝트) · uses(프로젝트→기준)
       · cites(기준→기준 조문 참조) · similar(문서↔문서 의미 유사)
       · of_program(문서→사업 정체) · same_program(같은 사업 계열 문서끼리)
       · same_law(같은 근거 법령 조항) · mentions(문서→개체) · relates(프로젝트 추정)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 임베딩 유사 간선 — 문서↔문서 연관의 주 신호(학습된 의미 표현).
# 개체 공출현(키워드 겹침)으로 문서를 잇는 방식은 폐기했다: 흔한 개체 하나
# (한국장학재단)로 성격이 다른 문서가 엮이고, 규칙으로 역할을 나누려 하면
# '업무처리기준'이 든 사업계획이 규정으로 오분류되는 등 사례 짜맞추기가 된다.
# 임베딩은 규칙 없이 의미로 묶는다 — 실측: 개인정보 동의절차끼리(0.86),
# 장학 사업끼리(0.84), IACF 규정끼리(0.90) 묶이고 절차↔사업은 안 엮인다.
# 이 값보다 가까운 문서 쌍만 잇는다(노드당 상위 2개). 0.55는 간선이 전체의
# 95%를 차지해 어수선했다(실측 52/54) → 강한 유사만 남긴다.
_SIM_THRESHOLD = 0.75
_SIM_TOP_K = 2
# 조문 참조 스캔 — 너무 짧은 제목은 오탐이 많아 제외
_MIN_TITLE_LEN = 3
# 같은 사업 계열 간선 — 문서 하나가 붙는 형제 문서 수 상한.
# 같은 사업에 문서가 36건이면 전부 이으면 630개가 된다(그래프가 읽히지 않는다).
# 사업 자체는 노드로 올려 전부 연결하고(선형), 문서끼리는 '역할이 다른' 형제 위주로
# 이 수만큼만 잇는다 — 공고와 그 서식·기본계획처럼 실제로 함께 보는 짝.
_SAME_PROGRAM_MAX = 4
# 같은 근거 법령 조항 — 이 문서 수를 넘겨 등장하는 조항은 근거가 아니라 배경이다
# (entities.hub_cutoff = √N 과 같은 규칙).
_LAW_HUB = None   # entities.hub_cutoff 로 계산

# 이유가 구조에서 바로 나오는 간선의 기본 문구 (화면에 그대로 나간다)
_DEFAULT_WHY = {
    "refers": "담당자가 이 문서의 근거 기준으로 지정했습니다.",
    "belongs": "같은 프로젝트에 속한 문서입니다.",
    "uses": "프로젝트가 이 기준 문서를 적용합니다.",
    "cites": "본문에 상대 문서의 제목이 인용되어 있습니다.",
}


def _josa(word: str, with_batchim: str = "을", without: str = "를") -> str:
    """받침 유무로 조사를 고른다 — 화면 문구가 '…재단을/…대학교를'로 맞게."""
    ch = (word or "").strip()[-1:]
    if not ch or not ("가" <= ch <= "힣"):
        return without
    return with_batchim if (ord(ch) - 0xAC00) % 28 else without


def _doc_node(d: dict, kind: str, chunk_counts: dict[int, int]) -> dict:
    label = d["filename"]
    for suffix in (".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".png", ".jpg"):
        if label.lower().endswith(suffix):
            label = label[: -len(suffix)]
            break
    return {
        "id": f"d{d['id']}",
        "doc_id": d["id"],
        "label": label,
        "kind": kind,
        "sector": d.get("sector") or "common",
        "doc_type": d.get("doc_type") or "auto",
        "chunks": chunk_counts.get(d["id"], 0),
    }


def build_graph(
    db, include_similarity: bool = True, dept: str | None = None, embed_fn=None,
) -> dict:
    """DB의 관계를 노드·간선 목록으로 만든다. 반환 형식은 /graph.json 계약.

    dept를 주면 그 부서 + 공통 문서만 그린다 — 부서별 지식 그래프
    (사용자 요구: 부서별로 나눠야 빠르고 정확).
    embed_fn(텍스트 목록 → 벡터)이 있으면 프로젝트↔문서 추정 연관을 잇는다;
    None 이면 검색 스택의 임베딩(embed_search.embed_texts)을 쓰고, 모델이 없으면 생략.
    """
    docs = db.list_documents()
    docs = [d for d in docs if d.get("doc_type") != "ocr"]  # OCR 작업물은 제외
    if dept:
        docs = [d for d in docs if (d.get("dept") or "공통") in (dept, "공통")]
    reg_counts = db.regulation_chunk_counts()

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(s: str, t: str, kind: str, w: float = 1.0,
                 why: str = "", evidence: list | None = None) -> None:
        """간선 하나 — 이유(why)와 근거 문구(evidence)를 함께 남긴다.

        why는 화면에 그대로 나가는 완결 문구다. 근거를 댈 수 없으면 간선을
        만들지 않는 것이 원칙이므로, why가 빈 간선은 구조 관계(지정·소속)뿐이다.
        """
        if s == t:
            return
        key = (min(s, t), max(s, t), kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edge = {"s": s, "t": t, "kind": kind, "w": round(w, 3),
                "reason": kind, "why": why or _DEFAULT_WHY.get(kind, "")}
        if evidence:
            edge["evidence"] = [str(e)[:120] for e in evidence[:3]]
        edges.append(edge)

    criteria = [d for d in docs if d["doc_type"] == "regulation"]
    intake = [d for d in docs if d["doc_type"] != "regulation"]
    doc_ids = {d["id"] for d in docs}

    for d in criteria:
        nodes.append(_doc_node(d, "criteria", reg_counts))
    for d in intake:
        nodes.append(_doc_node(d, "intake", reg_counts))

    # 프로젝트 노드 + 소속·적용 간선
    projects = db.list_all_projects()
    linked: set[tuple[int, int]] = set()          # (프로젝트, 문서) 명시 연결 — 추정 연관에서 제외
    for p in projects:
        nodes.append({
            "id": f"p{p['id']}",
            "doc_id": None,
            "label": p["name"],
            "kind": "project",
            "sector": p.get("sector") or "common",
            "doc_type": "project",
            "chunks": 0,
        })
        for cid in db.get_project_criteria_ids(p["id"]):
            if cid in doc_ids:
                add_edge(f"p{p['id']}", f"d{cid}", "uses")
                linked.add((p["id"], cid))

    # 접수 문서 → 기준 근거 간선 (지정 + 자동 제안)
    for d in intake:
        if d.get("project_id"):
            add_edge(f"d{d['id']}", f"p{d['project_id']}", "belongs")
            linked.add((int(d["project_id"]), d["id"]))
        if d.get("related_criteria_id") in doc_ids:
            add_edge(f"d{d['id']}", f"d{d['related_criteria_id']}", "refers")
        raw = d.get("suggested_criteria")
        if raw:
            try:
                for it in json.loads(raw):
                    cid = it.get("id") if isinstance(it, dict) else None
                    if cid in doc_ids:
                        add_edge(f"d{d['id']}", f"d{cid}", "refers", 0.7,
                                 why="검색이 이 기준 문서를 근거 후보로 제안했습니다.")
            except (ValueError, AttributeError):
                pass

    _add_citation_edges(db, criteria, add_edge)
    if include_similarity:
        _add_similarity_edges(db, criteria, add_edge)
    _add_relation_layer(db, nodes, docs, doc_ids, add_edge)
    _add_project_relations(db, docs, projects, linked, add_edge, embed_fn)

    return {"nodes": nodes, "edges": edges}


def _add_relation_layer(db, nodes: list[dict], docs: list[dict], doc_ids: set[int],
                        add_edge) -> dict | None:
    """사업 정체·법령 근거 계층 — 문서를 잇는 근거를 명시해서 만든다.

    ① 문서마다 '어떤 사업에 관한 문서인가'를 정하고(entities.corpus_profile)
       사업을 노드로 올린 뒤 of_program 간선을 건다 — 사업 하나에 문서가 많아도
       간선은 문서 수만큼만 늘어난다.
    ② 같은 사업 문서끼리는 '역할이 다른 짝'을 우선해 문서당 _SAME_PROGRAM_MAX개만
       잇는다(공고 ↔ 기본계획 ↔ 서식). 전부 잇지 않는 이유는 36문서짜리 사업이
       630개 간선이 되기 때문이다.
    ③ 근거 법령은 조항 단위로만 잇는다. 법률명만 같은 것은 근거가 아니다.
    """
    try:
        from zzaimy.graph.entities import (
            corpus_profile, document_role, hub_cutoff,
        )

        prof = corpus_profile(db)
    except Exception as e:
        log.warning("사업 정체 계층 생략 (%s: %s)", type(e).__name__, e)
        return None

    import math

    N = max(len(doc_ids), 1)
    identity = {d: v for d, v in prof["identity"].items() if d in doc_ids}
    programs = prof["programs"]
    roles = {d["id"]: document_role(d.get("filename") or "", prof.get("role_words", {}))
             for d in docs}

    # ① 사업 노드 + of_program
    members: dict = {}
    for did, (key, evidence) in identity.items():
        members.setdefault(key, []).append(did)
    for key, dids in members.items():
        p = programs[key]
        idf = math.log(1 + N / max(len(dids), 1))
        nodes.append({
            "id": f"g{abs(hash(key)) % 10**9}",
            "doc_id": None,
            "label": p.name,
            "kind": "entity",
            "sector": "common",
            "doc_type": "program",
            "chunks": len(dids),
            "importance": round(idf, 3),
        })
    node_of = {key: f"g{abs(hash(key)) % 10**9}" for key in members}
    for did, (key, evidence) in identity.items():
        add_edge(f"d{did}", node_of[key], "of_program", 1.0,
                 why=f"이 문서의 사업은 「{programs[key].name}」입니다.",
                 evidence=[evidence])

    # ② 같은 사업 문서끼리 — 역할이 다른 짝 우선
    for key, dids in members.items():
        name = programs[key].name
        for a in dids:
            others = [b for b in dids if b != a]
            others.sort(key=lambda b: (roles.get(b, "") == roles.get(a, ""), b))
            for b in others[:_SAME_PROGRAM_MAX]:
                ra, rb = roles.get(a) or "문서", roles.get(b) or "문서"
                add_edge(f"d{a}", f"d{b}", "same_program", 1.0,
                         why=f"같은 사업 「{name}」의 문서입니다 ({ra} ↔ {rb}).",
                         evidence=[identity[a][1], identity[b][1]])

    # ③ 같은 근거 법령 조항
    law_docs: dict = {}
    for did, typed in prof["typed"].items():
        if did not in doc_ids:
            continue
        for law in typed["law"]:
            if "제" in law and "조" in law:      # 조항까지 있는 것만 근거로 인정
                law_docs.setdefault(law, set()).add(did)
    cut = hub_cutoff(N)
    for law, dids in law_docs.items():
        if not (2 <= len(dids) <= cut):
            continue
        ordered = sorted(dids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 : i + 1 + _SAME_PROGRAM_MAX]:
                add_edge(f"d{a}", f"d{b}", "same_law", 0.8,
                         why=f"같은 근거 조항을 인용합니다 — {law}.",
                         evidence=[law])

    # ④ 남은 개체(기관 등) 언급 — 허브·역할 명칭을 걸러낸 것만
    linkable = prof["linkable"]
    ent_nodes: dict = {}
    for did, ents in prof["doc_entities"].items():
        if did not in doc_ids:
            continue
        for name, kind, n_mentions in ents:
            if (name, kind) not in linkable:
                continue
            df = linkable[(name, kind)]
            nid = ent_nodes.get((name, kind))
            if nid is None:
                nid = f"e{abs(hash((name, kind))) % 10**9}"
                ent_nodes[(name, kind)] = nid
                nodes.append({
                    "id": nid, "doc_id": None, "label": name, "kind": "entity",
                    "sector": "common", "doc_type": kind, "chunks": df,
                    "importance": round(math.log(1 + N / max(df, 1)), 3),
                })
            label = {"org": "기관", "law": "근거 법령", "program": "사업"}.get(kind, kind)
            add_edge(f"d{did}", nid, "mentions",
                     min(1.0, 0.3 + 0.12 * n_mentions),
                     why=f"이 문서가 {label} 「{name}」{_josa(name)} 언급합니다"
                         f" (코퍼스 {df}개 문서에만 나오는 이름).",
                     evidence=[name])
    return prof


def _add_citation_edges(db, criteria: list[dict], add_edge) -> None:
    """기준 문서 조각 본문에 다른 기준의 제목이 등장하면 참조 간선을 잇는다."""
    chunks = db.list_regulation_chunks()
    if not chunks:
        return
    # 문서별 대표 제목: 조각의 reg_title (없으면 건너뜀)
    title_of: dict[int, str] = {}
    for c in chunks:
        title_of.setdefault(c["doc_id"], (c.get("reg_title") or "").strip())
    titles = {
        did: t for did, t in title_of.items() if len(t) >= _MIN_TITLE_LEN
    }
    text_of: dict[int, list[str]] = {}
    for c in chunks:
        text_of.setdefault(c["doc_id"], []).append(c["content"])
    for src_id, parts in text_of.items():
        body = "\n".join(parts)
        for dst_id, title in titles.items():
            if dst_id == src_id:
                continue
            if title in body:
                add_edge(f"d{src_id}", f"d{dst_id}", "cites",
                         evidence=[title])


def _add_similarity_edges(db, criteria: list[dict], add_edge) -> None:
    """사전 계산 임베딩(npz)이 있으면 문서 평균 벡터의 코사인으로 유사 간선.

    준비물이 없으면 조용히 건너뛴다 (로컬 개발 환경 등).
    """
    try:
        import numpy as np

        from zzaimy.app.embed_search import INDEX_PATH

        if not Path(INDEX_PATH).exists():
            return
        data = np.load(INDEX_PATH)
        ids, vectors = data["ids"], data["vectors"]
        chunk_doc = {
            c["id"]: c["doc_id"] for c in db.list_regulation_chunks()
        }
        by_doc: dict[int, list] = {}
        for i, cid in enumerate(ids):
            did = chunk_doc.get(int(cid))
            if did is not None:
                by_doc.setdefault(did, []).append(vectors[i])
        if len(by_doc) < 2:
            return
        doc_ids = sorted(by_doc)
        mat = np.stack([np.mean(by_doc[d], axis=0) for d in doc_ids])
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        sim = mat @ mat.T
        for i, src in enumerate(doc_ids):
            order = np.argsort(sim[i])[::-1]
            picked = 0
            for j in order:
                if j == i or picked >= _SIM_TOP_K:
                    continue
                if sim[i][j] < _SIM_THRESHOLD:
                    break
                add_edge(f"d{src}", f"d{doc_ids[j]}", "similar", float(sim[i][j]),
                         why=f"문서 전체 의미가 가깝습니다 (코사인 {float(sim[i][j]):.2f}).")
                picked += 1
    except Exception as e:  # 그래프는 부가 기능 — 유사 간선 실패가 전체를 막지 않게
        log.warning("유사 간선 생략 (%s: %s)", type(e).__name__, e)


# 프로젝트↔문서 추정 연관 — 명시 연결(소속·적용)이 없어도 프로젝트가 무엇과 관련되는지
# 보이게 한다. 프로젝트 텍스트(이름·지침·연결 기준 제목)와 문서 벡터의 코사인이 전체
# 분포에서 돋보이는 문서(평균 + 1σ 이상, 상위 _REL_TOP_K)만 잇는다 — 절대 임계값이 아니라
# 프로젝트마다 스스로 정규화되는 규칙(ADR-0015). 간선은 '추정'으로 표시한다.
_REL_TOP_K = 6
_REL_STD_MULT = 1.0
# 코사인 바닥값 — 돋보임 규칙만으로는 문서가 3~4개뿐인 작은 그래프에서 '가장 덜 먼' 문서가
# 뽑힌다. KURE 실측(2026-09-14)에서 무관 쌍은 대체로 0.3~0.47이라 0.5 아래는 잇지 않는다.
# 초기값이며 실물 프로젝트가 쌓이면 재보정 대상(미확인).
_REL_MIN_SIM = 0.5
_REL_MIN_TEXT = 6           # 이름·지침이 이보다 짧은 프로젝트("ㅇㅇ")는 추정할 근거가 없다
_REL_WARM_MAX_SYNC = 3      # 이보다 많은 문서 벡터를 새로 계산해야 하면 배경에서 하고 이번엔 생략
_warm_lock = __import__("threading").Lock()
_warming: set[str] = set()


def _warm_vectors_background(cache_path: Path, texts: dict, embed_fn) -> None:
    """문서 벡터 계산은 CPU에서 문서당 수 초 — 요청 경로를 막지 않게 데몬 스레드로."""
    import threading

    from zzaimy.graph.doc_vectors import DocVectorCache

    key = str(cache_path)
    with _warm_lock:
        if key in _warming:
            return
        _warming.add(key)

    def run() -> None:
        try:
            DocVectorCache(cache_path).vectors(texts, embed_fn)
        except Exception as e:
            log.warning("문서 벡터 배경 계산 실패 (%s: %s)", type(e).__name__, e)
        finally:
            with _warm_lock:
                _warming.discard(key)

    threading.Thread(target=run, name="doc-vectors-warm", daemon=True).start()


def _add_project_relations(db, docs, projects, linked, add_edge, embed_fn=None,
                           background: bool = True) -> None:
    if not projects or not docs:
        return
    if embed_fn is None:
        try:
            from zzaimy.app.embed_search import embed_texts as embed_fn
        except Exception:
            return
    try:
        import numpy as np

        from zzaimy.graph.doc_vectors import DocVectorCache, doc_text, project_text

        texts = {d["id"]: doc_text(db, d) for d in docs}
        texts = {i: t for i, t in texts.items() if len(t) >= 2}   # 제목만 있어도 주제는 있다
        if not texts:
            return
        cache_path = Path(db.path).parent / "doc_vectors.npz"
        cache = DocVectorCache(cache_path)
        if background and cache.missing(texts) > _REL_WARM_MAX_SYNC:
            _warm_vectors_background(cache_path, texts, embed_fn)   # 다음 요청부터 추정 연관이 보인다
            return
        ids, mat = cache.vectors(texts, embed_fn)
        if mat is None or not ids:
            return
        by_id = {d["id"]: d for d in docs}
        for p in projects:
            crit = [by_id[c] for c in db.get_project_criteria_ids(p["id"]) if c in by_id]
            ptext = project_text(db, p, crit)
            if len(__import__("re").sub(r"[\W_]+", "", ptext)) < _REL_MIN_TEXT:
                continue                              # 근거 없는 이름뿐이면 추정하지 않는다
            pv = embed_fn([ptext])
            if pv is None:
                return
            sims = mat @ np.asarray(pv, dtype="float32")[0]
            if len(sims) < 3:
                cut = float(sims.max())
            else:
                cut = float(sims.mean() + _REL_STD_MULT * sims.std())
            order = np.argsort(-sims)
            picked = 0
            for j in order:
                if picked >= _REL_TOP_K or float(sims[j]) < max(cut, _REL_MIN_SIM):
                    break
                did = ids[j]
                if (p["id"], did) in linked:
                    continue
                add_edge(f"p{p['id']}", f"d{did}", "relates", float(sims[j]),
                         why=f"프로젝트 설명과 문서 내용의 의미가 가깝습니다"
                             f" (코사인 {float(sims[j]):.2f}, 추정).")
                picked += 1
    except Exception as e:  # 그래프는 부가 기능 — 추정 연관 실패가 전체를 막지 않게
        log.warning("프로젝트 추정 연관 생략 (%s: %s)", type(e).__name__, e)
