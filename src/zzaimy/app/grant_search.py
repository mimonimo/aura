"""사업 문서 계열 검색 — 국고 사업 문서(계획서·실적보고서·평가·지침 …)의 조각을 찾는다(ADR-0049).

절대 규칙 6: 문서 계열을 단일 색인으로 합치지 않는다 — 규정 계열(regulation_chunks·chunk_embeddings.npz)과 따로 둔다.
들어온 길(DGX 원본 동기화·DGX 가벼운 처리·문서함 업로드·채팅 첨부)과 상관없이 문서함의 사업 문서(doc_type grant) 조각은 모두 이 색인에
들어간다 — 1분 주기 동기화(scripts/170 --quick)가 새 조각만 임베딩해 덧붙인다(build_increment).

순서(에이전트 리즈닝 앞 단계와 같은 꼴, graph/retrieve):
  ① 그래프로 질문의 사업·연차·갈래를 좁힌다 — 그 사업 문서들의 조각만 후보(사업을 못 찾으면 전체)
  ② 후보 안에서 어휘(Kiwi 명사·희소 가중) 순위와 임베딩(Embed v2, 규정 계열과 같은 모델) 순위를 RRF 로 섞는다
  ③ 상위 조각을 사업 > 연차 > 문서 > 절 경로와 함께 낸다
열람: 문서의 access_level 이 public 이거나 본인 소유만(규정 계열과 같은 원칙, 절대 규칙 4 — 검색 단계에서 거른다).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from zzaimy.app import paths as _paths

log = logging.getLogger(__name__)
INDEX = _paths.index_npz().parent / "grant_embeddings.npz"
TEXT_KINDS = ("text", "table", "image_text")
TOP_K = 60
_lock = threading.Lock()
_cache: dict = {"mtime": None, "ids": None, "vecs": None}


def _text(c: dict) -> str:
    if c.get("kind") == "table":
        from zzaimy.app.render import table_text
        try:
            return table_text(c.get("content") or "")
        except Exception:
            return str(c.get("content") or "")
    return str(c.get("content") or "")


def corpus(db, doc_ids: set[int] | None = None, user: str | None = None) -> list[dict]:
    """사업 문서 조각 — 글이 있는 조각만, 열람 가능한 문서만."""
    q = ("SELECT c.id, c.doc_id, c.seq, c.kind, c.content, d.filename, d.owner, d.access_level FROM doc_chunks c"
         " JOIN documents d ON d.id = c.doc_id WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?)")
    with db._conn() as conn:
        rows = conn.execute(q, TEXT_KINDS).fetchall()
    out = []
    for r in rows:
        did = int(r[1])
        if doc_ids is not None and did not in doc_ids:
            continue
        if user is not None and (r[7] or "public") != "public" and (r[6] or "") != user:
            continue
        c = {"id": int(r[0]), "doc_id": did, "seq": int(r[2]), "kind": r[3], "filename": r[5]}
        c["content"] = _text({"kind": r[3], "content": r[4]})
        out.append(c)
    return out


PREV = INDEX.with_suffix(".prev.npz")


def _read(path: Path):
    import numpy as np
    with np.load(path) as z:
        ids, vecs = z["ids"], z["vectors"]
    if vecs.ndim != 2 or len(ids) != len(vecs):
        raise ValueError(f"색인 모양이 맞지 않음: ids {len(ids)} · vectors {vecs.shape}")
    return ids, vecs


def _load():
    """색인 읽기 — 깨졌으면 직전 정상본(.prev), 그것도 없으면 (None, None)(검색은 어휘 단독으로 계속)."""
    if not INDEX.exists():
        return None, None
    mt = INDEX.stat().st_mtime
    with _lock:
        if _cache["mtime"] != mt:
            try:
                ids, vecs = _read(INDEX)
            except Exception as e:
                log.warning("사업 문서 색인 읽기 실패(%s) — 직전 정상본으로", type(e).__name__)
                try:
                    ids, vecs = _read(PREV)
                except Exception:
                    return None, None
            _cache.update(mtime=mt, ids=ids, vecs=vecs)
        return _cache["ids"], _cache["vecs"]


def _encode(texts: list[str], timeout: float | None = None):
    """토르의 임베딩 서비스(Embed v2, 규정 계열·질의와 같은 모델). 없으면 예외 — 부르는 쪽이 어휘 단독으로 간다."""
    from zzaimy.app.embed_search import remote_vectors
    v = remote_vectors(texts, timeout=timeout)
    if v is None:
        raise RuntimeError("임베딩 서비스 없음")
    return v


BATCH_TIMEOUT = 90.0   # 묶음 색인 — 토르가 검토·판독으로 바쁘면 64조각에 8초(질의 기본)를 넘긴다(2026-10-02 실측)


def _encode_batch(texts: list[str]):
    """묶음 임베딩 — 실패하면 반으로 나눠 다시. 한 조각만 남아도 실패하면 그 자리는 None(색인에서 빼고 다음 회차에 다시)."""
    try:
        return list(_encode(texts, timeout=BATCH_TIMEOUT))
    except RuntimeError:
        if len(texts) == 1:
            return [None]
        mid = len(texts) // 2
        return _encode_batch(texts[:mid]) + _encode_batch(texts[mid:])


def dense_ids(question: str, allowed: set[int], top_k: int = TOP_K) -> list[int]:
    ids, vecs = _load()
    if ids is None or not len(ids):
        return []
    try:
        qv = _encode([question])[0]
    except Exception as e:  # 임베딩 서비스가 없으면 어휘 단독
        log.warning("사업 문서 임베딩 질의 실패: %s", e)
        return []
    import numpy as np
    sims = vecs @ np.asarray(qv)
    order = np.argsort(-sims)
    out = []
    for i in order:
        cid = int(ids[i])
        if cid in allowed:
            out.append(cid)
            if len(out) >= top_k:
                break
    return out


def search(db, question: str, k: int = 6, user: str | None = None) -> dict:
    """질문 → {steps, hits:[{chunk_id, doc_id, content, path, score}]}. steps 는 리즈닝 단계 기록(graph/retrieve 와 같은 말)."""
    from zzaimy.app.embed_search import rrf_merge
    from zzaimy.app.regulations import _lexical_ids, extract_nouns
    from zzaimy.graph import kg_store
    from zzaimy.graph import retrieve as gr

    steps: list[str] = []
    scope_docs: set[int] | None = None
    path_of: dict[int, list[str]] = {}
    try:
        tr = gr.retrieve(db, question, k=200)
        steps += tr.steps[:2]
        if tr.program:
            nodes = {n["id"]: n for n in kg_store.nodes(db, "doc")}
            under: set[str] = set()
            frontier = [tr.program]
            contains = kg_store.edges(db, "contains")
            kids: dict[str, list[str]] = {}
            for e in contains:
                kids.setdefault(e["src"], []).append(e["dst"])
            while frontier:
                x = frontier.pop()
                for y in kids.get(x, []):
                    if y.startswith(("year:", "doc:")) and ":sec:" not in y and y not in under:
                        under.add(y)
                        frontier.append(y)
            scope_docs = {int(nodes[d]["doc_id"]) for d in under if d in nodes and nodes[d].get("doc_id")}
            for h in tr.hits:
                path_of.setdefault(h.doc_id, h.path[:3])
    except Exception as e:
        log.warning("그래프 좁히기 실패: %s", e)
    chunks = corpus(db, scope_docs, user)
    if not chunks and scope_docs is not None:
        steps.append("그 사업의 문서 조각이 없어 사업 문서 전체에서 찾는다")
        chunks = corpus(db, None, user)
    allowed = {c["id"] for c in chunks}
    query = extract_nouns(question)
    lex = _lexical_ids(query, chunks, 1) if query else []
    den = dense_ids(question, allowed)
    merged = rrf_merge(lex[:TOP_K], den, w_a=0.4, w_b=1.0) if den else lex
    by_id = {c["id"]: c for c in chunks}
    hits = []
    for cid in merged[: k * 3]:
        c = by_id.get(cid)
        if not c:
            continue
        hits.append({"chunk_id": cid, "doc_id": c["doc_id"], "content": c["content"][:1200], "filename": c["filename"],
                     "path": path_of.get(c["doc_id"], []) + [c["filename"]]})
        if len(hits) >= k:
            break
    steps.append(f"[3단계: 근거 선택] 사업 문서 조각 {len(chunks)}개 중 어휘 {len(lex)}·임베딩 {len(den)} 순위를 섞어 {len(hits)}개")
    return {"steps": steps, "hits": hits}


def build_increment(db, batch: int = 128, limit: int = 20000) -> dict:
    """색인에 없는 사업 문서 조각만 임베딩해 덧붙인다(지워진 조각은 뺀다). 글 = 문서 이름 + 본문 1200자(규정 계열과 같은 길이)."""
    import fcntl

    import numpy as np
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    # 색인 쓰기는 한 번에 하나 — 부르는 길(1분 주기·색인 주기)과 상관없이 색인 파일 옆 잠금으로(2026-10-02: 두 쓰기가 섞여 2GB 색인이 깨졌다)
    lockf = open(INDEX.with_suffix(".lock"), "a")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lockf.close()
        return {"added": 0, "removed": 0, "total": 0, "pending": 0, "busy": True}
    try:
        return _build_increment(db, batch, limit)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


def _build_increment(db, batch: int, limit: int) -> dict:
    import os

    import numpy as np
    _cache["mtime"] = None                                 # 디스크의 현재 판으로 시작
    ids, vecs = _load()
    have = set(int(i) for i in ids) if ids is not None else set()
    # 번호만 먼저 — 본문은 새로 넣을 조각만 읽는다(조각 50만 개를 매번 통째로 읽지 않게)
    with db._conn() as conn:
        live = {int(r[0]) for r in conn.execute(
            "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?)", TEXT_KINDS).fetchall()}
    todo = sorted(live - have)
    new = _fetch(db, todo[:limit])
    keep_mask = np.array([int(i) in live for i in ids]) if ids is not None and len(ids) else None
    out_ids = ids[keep_mask] if keep_mask is not None else np.zeros((0,), dtype=np.int64)
    out_vecs = vecs[keep_mask] if keep_mask is not None else None
    added = 0
    failed = 0
    for i in range(0, len(new), batch):
        part = new[i:i + batch]
        got = _encode_batch([f"{c['filename']}\n{c['content'][:1200]}" for c in part])
        keep = [(c, v) for c, v in zip(part, got) if v is not None]
        failed += len(part) - len(keep)
        if not keep:
            if failed >= batch * 3:
                raise RuntimeError("임베딩 서비스 없음")   # 서비스가 아예 안 되면 이번 회차는 여기까지
            continue
        v = np.asarray([x for _c, x in keep], dtype=np.float32)
        out_ids = np.concatenate([out_ids, np.array([c["id"] for c, _x in keep], dtype=np.int64)])
        out_vecs = v if out_vecs is None else np.vstack([out_vecs, v])
        added += len(keep)
    removed = (len(ids) - int(keep_mask.sum())) if keep_mask is not None else 0
    if added or removed:
        tmp = INDEX.with_name(f".{INDEX.stem}.{os.getpid()}.tmp.npz")
        # 압축하지 않는다 — 벡터는 거의 줄지 않고, 수 GB 를 회차마다 압축하는 게 색인 속도를 깎았다(10/2 실측 초당 20여 조각)
        np.savez(tmp, ids=out_ids, vectors=out_vecs if out_vecs is not None else np.zeros((0, 1)))
        try:
            _read(tmp)                                     # 다시 읽어 검사한 뒤에만 바꾼다
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if INDEX.exists():
            try:
                _read(INDEX)
                INDEX.replace(PREV)                        # 직전 정상본을 남긴다
            except Exception:
                pass                                       # 깨진 것은 정상본으로 남기지 않는다
        tmp.replace(INDEX)
        (INDEX.with_suffix(".json")).write_text(json.dumps({"n_chunks": int(len(out_ids)), "added": added, "removed": removed},
                                                           ensure_ascii=False), encoding="utf-8")
    return {"added": added, "removed": removed, "total": int(len(out_ids)), "pending": max(0, len(todo) - limit)}


def _fetch(db, chunk_ids: list[int]) -> list[dict]:
    """조각 번호 → 색인용 글(문서 이름 포함)."""
    out = []
    with db._conn() as conn:
        for i in range(0, len(chunk_ids), 500):
            part = chunk_ids[i:i + 500]
            rows = conn.execute(
                "SELECT c.id, c.kind, c.content, d.filename FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
                f" WHERE c.id IN ({','.join('?' * len(part))})", part).fetchall()
            for r in rows:
                out.append({"id": int(r[0]), "filename": r[3], "content": _text({"kind": r[1], "content": r[2]})})
    out.sort(key=lambda c: c["id"])
    return out
