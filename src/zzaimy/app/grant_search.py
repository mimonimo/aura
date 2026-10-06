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
import os
import re
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


def corpus(db, doc_ids: set[int] | None = None, user: str | None = None, depts: list[str] | None = None) -> list[dict]:
    """사업 문서 조각 — 글이 있는 조각만, 열람 가능한 문서만(+ RAG 공간의 부서)."""
    dsql, dargs = _access(None, depts)
    q = ("SELECT c.id, c.doc_id, c.seq, c.kind, c.content, d.filename, d.owner, d.access_level FROM doc_chunks c"
         " JOIN documents d ON d.id = c.doc_id WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?)" + dsql)
    with db._conn() as conn:
        rows = conn.execute(q, (*TEXT_KINDS, *dargs)).fetchall()
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


def _check(path: Path) -> int:
    """색인 파일 검사(벡터 본체는 읽지 않는다) — 번호 배열과 벡터 머리의 모양, 벡터 본체 길이가 맞는지. 조각 수를 돌려준다.

    10/4 VM 메모리 부족(OOM 세 번·멈춤 한 번)의 한 원인: 5.4GB 색인을 쓰고 나서 검사하느라 통째로 두 번 더 읽었다."""
    import zipfile

    import numpy as np
    with zipfile.ZipFile(path) as zf:
        with zf.open("ids.npy") as f:
            ids = np.lib.format.read_array(f)
        info = zf.getinfo("vectors.npy")
        with zf.open(info) as f:
            version = np.lib.format.read_magic(f)
            reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
            shape, _fortran, dtype = reader(f)
            head = f.tell()
    if len(shape) != 2 or shape[0] != len(ids):
        raise ValueError(f"색인 모양이 맞지 않음: ids {len(ids)} · vectors {shape}")
    if info.file_size - head < shape[0] * shape[1] * np.dtype(dtype).itemsize:
        raise ValueError("색인 벡터 본체가 잘렸음")
    return int(len(ids))


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


PG_EF_SEARCH = 300          # 대결(178, 2026-10-06): ef 300 에서 recall@10 0.915·@50 0.963, 질의 15ms


def _dense_pg(db, qv, top_k: int, scope_docs: set[int] | None, user: str | None, depts: list[str] | None,
              allowed: set[int] | None = None) -> list[int] | None:
    """pgvector(표 grant_vec, halfvec + HNSW) 로 조밀 순위 — 권한·부서·범위를 같은 SQL 에서 거른다. 못 쓰면 None(배열 방식으로).
    범위(문서)·허용 조각 목록은 배열 하나로 넘긴다(= ANY(?)) — 사업 하나가 문서 1만 건을 넘어 인자 수 한도에 걸리지 않게."""
    if (scope_docs is not None and not scope_docs) or (allowed is not None and not allowed):
        return []
    acc, args = _access(user, depts)
    vec = "[" + ",".join(f"{float(x):.5f}" for x in qv) + "]"
    scope_sql = ""
    if scope_docs is not None:
        scope_sql += " AND g.doc_id = ANY(?)"
        args = [*args, sorted(int(x) for x in scope_docs)]
    if allowed is not None:
        scope_sql += " AND g.chunk_id = ANY(?)"
        args = [*args, sorted(int(x) for x in allowed)]
    try:
        with db._conn() as conn:
            if getattr(conn, "dialect", "") != "postgres":
                return None
            conn.execute(f"SET hnsw.ef_search = {PG_EF_SEARCH}")
            conn.execute("SET hnsw.iterative_scan = relaxed_order")      # 거르는 조건이 있어도 k 개를 채운다
            rows = conn.execute(
                "SELECT g.chunk_id FROM grant_vec g JOIN documents d ON d.id = g.doc_id"
                f" WHERE d.doc_type = 'grant' AND d.status = 'reviewed'{acc}{scope_sql}"
                " ORDER BY g.emb <#> ?::halfvec LIMIT ?", [*args, vec, int(top_k)]).fetchall()
        return [int(r[0]) for r in rows]
    except Exception as e:                                              # 표·확장이 없거나 연결 문제 — 배열 방식으로
        log.warning("pgvector 조밀 검색 실패(%s) — 배열 방식으로", type(e).__name__)
        return None


def dense_ids(question: str, allowed: set[int] | None, top_k: int = TOP_K, scope_docs: set[int] | None = None,
              user: str | None = None, db=None, pool: int = 3000, depts: list[str] | None = None) -> list[int]:
    """임베딩 순위. allowed 를 주면 그 조각만(옛 경로). 아니면 유사도 상위 pool 개를 뽑고 범위·열람 권한은 SQL 로 거른다 —
    범위가 좁으면(사업 하나) 그 범위의 조각 번호만 놓고 유사도를 잰다."""
    try:
        qv = _encode([question])[0]
    except Exception as e:  # 임베딩 서비스가 없으면 어휘 단독
        log.warning("사업 문서 임베딩 질의 실패: %s", e)
        return []
    # 기본은 pgvector(ADR-0057) — 배열 파일(5GB 넘음)을 앱 메모리에 올리지 않는다. 표가 없으면(개발 환경·SQLite) 배열로
    if db is not None and os.environ.get("ZZAIMY_DENSE_BACKEND", "pgvector") == "pgvector":
        got = _dense_pg(db, qv, top_k, scope_docs, user, depts, allowed=allowed)
        if got is not None:
            return got
    ids, vecs = _load()
    if ids is None or not len(ids):
        return []
    import numpy as np
    q = np.asarray(qv, dtype=np.float32)
    if allowed is not None:
        sims = vecs @ q
        out = []
        for i in np.argsort(-sims):
            cid = int(ids[i])
            if cid in allowed:
                out.append(cid)
                if len(out) >= top_k:
                    break
        return out
    if scope_docs is not None:
        in_scope = scoped_chunk_ids(db, scope_docs, user, depts)
        if not in_scope:
            return []
        pos = _positions(ids)
        idx = np.array([pos[c] for c in in_scope if c in pos], dtype=np.int64)
        if not len(idx):
            return []
        sims = vecs[idx] @ q
        order = idx[np.argsort(-sims)[:top_k]]
        return [int(ids[i]) for i in order]
    sims = vecs @ q
    top = np.argpartition(-sims, min(pool, len(sims) - 1))[:pool]
    top = top[np.argsort(-sims[top])]
    cand = [int(ids[i]) for i in top]
    ok = permitted(db, cand, user, depts)
    return [c for c in cand if c in ok][:top_k]


def _positions(ids) -> dict[int, int]:
    """조각 번호 → 색인 행 — 색인 판(mtime)마다 한 번 만든다."""
    with _lock:
        if _cache.get("pos_mtime") != _cache.get("mtime") or _cache.get("pos") is None:
            _cache["pos"] = {int(c): i for i, c in enumerate(ids)}
            _cache["pos_mtime"] = _cache.get("mtime")
        return _cache["pos"]


def _access(user: str | None, depts: list[str] | None = None) -> tuple[str, list]:
    """열람 권한(+ RAG 공간의 부서, ADR-0053) 조건. depts 가 None 이면 부서로 자르지 않는다.
    범위 밖 갈래(지출·계약 증빙, 절대 규칙 11 — doc_routing.excluded_kinds)는 여기서 늘 뺀다(어휘·의미·범위 검색이 모두 이 조건을 쓴다)."""
    from zzaimy.app.doc_routing import excluded_kinds
    sql, args = "", []
    ex = excluded_kinds()
    if ex:
        sql += f" AND COALESCE(d.kind, '') NOT IN ({','.join('?' * len(ex))})"
        args += list(ex)
    if user is not None:
        sql += " AND (COALESCE(d.access_level, 'public') = 'public' OR COALESCE(d.owner, '') = ?)"
        args.append(user)
    if depts is not None:
        if not depts:
            return " AND 1 = 0", []
        sql += f" AND COALESCE(d.dept, '공통') IN ({','.join('?' * len(depts))})"
        args += list(depts)
    return sql, args


def scoped_chunk_ids(db, scope_docs: set[int], user: str | None, depts: list[str] | None = None) -> list[int]:
    acc, args = _access(user, depts)
    ids = sorted(scope_docs)
    out: list[int] = []
    with db._conn() as conn:
        for i in range(0, len(ids), 5000):
            part = ids[i:i + 5000]
            out += [int(r[0]) for r in conn.execute(
                "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id WHERE d.doc_type = 'grant' AND d.status = 'reviewed'"
                f" AND c.kind IN (?, ?, ?) AND c.doc_id IN ({','.join('?' * len(part))})" + acc,
                (*TEXT_KINDS, *part, *args)).fetchall()]
    return out


def permitted(db, chunk_ids: list[int], user: str | None, depts: list[str] | None = None) -> set[int]:
    if not chunk_ids:
        return set()
    acc, args = _access(user, depts)
    with db._conn() as conn:
        return {int(r[0]) for r in conn.execute(
            "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id WHERE d.doc_type = 'grant' AND d.status = 'reviewed'"
            f" AND c.kind IN (?, ?, ?) AND c.id IN ({','.join('?' * len(chunk_ids))})" + acc,
            (*TEXT_KINDS, *chunk_ids, *args)).fetchall()}


def chunks_by_ids(db, chunk_ids: list[int]) -> list[dict]:
    """최종 후보 조각만 본문까지 읽는다."""
    if not chunk_ids:
        return []
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.doc_id, c.kind, c.content, d.filename, c.seq FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
            f" WHERE c.id IN ({','.join('?' * len(chunk_ids))})", list(chunk_ids)).fetchall()
    return [{"id": int(r[0]), "doc_id": int(r[1]), "kind": r[2], "content": _text({"kind": r[2], "content": r[3]}),
             "filename": r[4], "seq": int(r[5])} for r in rows]


PER_DOC = 2
_QUOTED = re.compile(r"[「『\"“]([^」』\"”]{2,60})[」』\"”]")


def section_hits(db, question: str, scope_docs: set[int] | None, limit: int = 12) -> list[int]:
    """질문에 묶어 적은 절 이름(「행사 개요」)과 제목이 같은 절의 첫 조각 — 범위(사업·연차·갈래) 안 문서에서 그래프 절 노드로.
    어휘·임베딩 후보 30개에 같은 제목 절이 하나도 못 들던 것(10/5: 같은 해 19개 문서에 있는 「행사 개요」를 놓침)."""
    phrases = [re.sub(r"\s+", "", m) for m in _QUOTED.findall(question or "")]
    if not phrases or not scope_docs or len(phrases[0]) < 2:
        return []
    ids = sorted(scope_docs)[:5000]
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT doc_id, label, props FROM kg_nodes WHERE type = 'section'"
            f" AND doc_id IN ({','.join('?' * len(ids))}) AND REPLACE(label, ' ', '') LIKE ? LIMIT 200",
            (*ids, f"%{phrases[0]}%")).fetchall()
        picked: list[tuple[int, int]] = []
        for did, label, props in rows:
            core = re.sub(r"^[\s\dⅠ-Ⅹ.()가-하\-]{0,6}", "", re.sub(r"\s+", "", label or ""))
            if core != phrases[0] and len(core) > len(phrases[0]) + 6:
                continue                                    # 제목이 그 이름이거나 번호만 붙은 것만(긴 제목 속 일부는 아니다)
            seqs = (json.loads(props or "{}").get("chunks") or [])[:1]
            if seqs:
                picked.append((int(did), int(seqs[0])))
            if len(picked) >= limit:
                break
        out: list[int] = []
        for did, seq in picked:
            r = conn.execute("SELECT id FROM doc_chunks WHERE doc_id = ? AND seq = ?", (did, seq)).fetchone()
            if r:
                out.append(int(r[0]))
    return out


def rerank_hits(question: str, chunks: list[dict]) -> list[dict]:
    """질문에 묶어 적은 문구(「절 이름」·"…")가 있으면 그 문구가 든 조각을 앞으로 — 순서는 그 안에서 그대로(안정 정렬)."""
    phrases = [re.sub(r"\s+", "", m) for m in _QUOTED.findall(question or "")]
    if not phrases:
        return chunks
    def has(c):
        t = re.sub(r"\s+", "", c.get("content") or "")
        return any(p and p in t for p in phrases)
    return sorted(chunks, key=lambda c: not has(c))


EXPAND_BELOW = 400
EXPAND_TO = 1200


def quality_filter(chunks: list[dict]) -> list[dict]:
    """근거가 될 수 없는 조각(목차·쪽 번호·정형 문구·같은 본문 중복)을 뺀다 — 규정 검색과 같은 품질 판정(SEARCH 강도).
    이웃 확장 뒤에 판정한다(제목 조각은 본문이 붙어 살아남고, 목차는 확장해도 목차). 다 빠지면 그대로 둔다.
    10/5 RAG 실측: 절 이름이 그대로 든 목차 줄이 걸려 답이 「제목만 있고 본문이 없다」로 끝났다."""
    from zzaimy.app.chunk_quality import Strictness, assess
    seen: set[str] = set()
    out = []
    for c in chunks:
        body = re.sub(r"\s+", " ", c.get("content") or "").strip()
        if not body or body in seen:
            continue
        seen.add(body)
        try:
            ok = assess(c.get("content") or "").keep(Strictness.SEARCH)
        except Exception:
            ok = True
        if ok:
            out.append(c)
    return out or chunks


def expand_many(db, chunks: list[dict]) -> None:
    """짧은 조각들의 뒤 조각을 한 번에 가져와 content 를 늘린다(제자리)."""
    short = [c for c in chunks if len(c.get("content") or "") < EXPAND_BELOW and c.get("seq") is not None]
    if not short:
        return
    # 조각마다 바로 뒤 6개까지만(큰 문서의 조각 전체를 읽지 않게)
    cond = " OR ".join("(doc_id = ? AND seq > ? AND seq <= ?)" for _ in short)
    args = [x for c in short for x in (c["doc_id"], c["seq"], c["seq"] + 6)]
    with db._conn() as conn:
        rows = conn.execute(f"SELECT doc_id, seq, kind, content FROM doc_chunks WHERE ({cond}) AND kind IN (?, ?, ?)"
                            " ORDER BY doc_id, seq", (*args, *TEXT_KINDS)).fetchall()
    by_doc: dict[int, list[tuple[int, str]]] = {}
    for r in rows:
        by_doc.setdefault(int(r[0]), []).append((int(r[1]), _text({"kind": r[2], "content": r[3]})))
    for c in short:
        text = c.get("content") or ""
        for seq, t in by_doc.get(c["doc_id"], []):
            if seq <= c["seq"]:
                continue
            if len(text) >= EXPAND_TO:
                break
            text += "\n" + t
        c["content"] = text[:EXPAND_TO]


def expand(db, hit: dict) -> str:
    """걸린 조각이 짧으면(절 제목·한 줄) 같은 문서의 뒤 조각을 이어 본문까지 — 제목만 건네면 답이 「내용을 확인하기 어렵다」로
    끝났다(10/5 RAG 실측 답변 10개 중 다수). 이웃 조각 확장."""
    text = hit.get("content") or ""
    if len(text) >= EXPAND_BELOW or hit.get("seq") is None:
        return text
    with db._conn() as conn:
        rows = conn.execute("SELECT kind, content FROM doc_chunks WHERE doc_id = ? AND seq > ? AND kind IN (?, ?, ?) ORDER BY seq LIMIT 6",
                            (hit["doc_id"], hit["seq"], *TEXT_KINDS)).fetchall()
    for r in rows:
        if len(text) >= EXPAND_TO:
            break
        text += "\n" + _text({"kind": r[0], "content": r[1]})
    return text[:EXPAND_TO]


def docs_under(db, program: str) -> set[int]:
    """그래프에서 사업 노드 아래(연차 경유) 문서 번호 — 관계 전체를 읽지 않고 SQL 로."""
    with db._conn() as conn:
        years = [r[0] for r in conn.execute("SELECT dst FROM kg_edges WHERE kind = 'contains' AND src = ? AND dst LIKE 'year:%'",
                                            (program,)).fetchall()]
        srcs = [program, *years]
        rows = conn.execute(
            "SELECT n.doc_id FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst WHERE e.kind = 'contains'"
            f" AND e.src IN ({','.join('?' * len(srcs))}) AND e.dst LIKE 'doc:%' AND n.doc_id IS NOT NULL", srcs).fetchall()
    return {int(r[0]) for r in rows}


def search(db, question: str, k: int = 6, user: str | None = None, prefer_docs: set[int] | None = None,
           depts: list[str] | None = None) -> dict:
    """질문 → {steps, hits:[{chunk_id, doc_id, content, path, score}]}. steps 는 리즈닝 단계 기록(graph/retrieve 와 같은 말)."""
    from zzaimy.app.embed_search import rrf_merge
    from zzaimy.app.regulations import _lexical_ids, extract_nouns
    from zzaimy.graph import kg_store
    from zzaimy.graph import retrieve as gr

    steps: list[str] = []
    scope_docs: set[int] | None = None
    program_docs: set[int] | None = None
    path_of: dict[int, list[str]] = {}
    try:
        sc = gr.scope(db, question)
        steps += sc.steps
        scope_docs, program_docs, path_of = sc.docs, sc.program_docs, sc.path_of
    except Exception as e:
        log.warning("그래프 좁히기 실패: %s", e)
    if prefer_docs:
        # 프로젝트가 참조로 붙인 과거 사업 묶음의 문서부터(그래프로 좁힌 범위와 겹치면 그 겹침, 아니면 묶음 전체)
        narrowed = (prefer_docs & scope_docs) if scope_docs else set()
        scope_docs = narrowed or set(prefer_docs)
        steps.append(f"[참조 보관 사업] 프로젝트가 참조한 과거 사업 문서 {len(scope_docs)}건에서 먼저 찾는다")
    from zzaimy.app import grant_lex
    query = extract_nouns(question)
    have, want = grant_lex.coverage(db)
    if want and have >= 0.5 * want:
        # 색인 경로 — 후보 조각만 읽는다(범위·열람 권한은 SQL 에서). 색인에 아직 없는 새 조각은 임베딩 축이 찾는다 —
        # 95% 아래로 옛 경로(조각 전체 읽기)로 물러나면 새 문서가 들어오는 동안 검색이 수십 초로 느려졌다(10/5 94.4%)
        lex = grant_lex.rank(db, query, scope_docs, user, depts=depts) if query else []
        den = dense_ids(question, None, scope_docs=scope_docs, user=user, db=db, depts=depts)
        if not lex and not den and program_docs and scope_docs is not None and scope_docs != program_docs:
            steps.append("좁힌 문서에서 맞는 조각이 없어 그 사업 문서 전체에서 찾는다")
            lex = grant_lex.rank(db, query, program_docs, user, depts=depts) if query else []
            den = dense_ids(question, None, scope_docs=program_docs, user=user, db=db, depts=depts)
        if not lex and not den and scope_docs is not None:
            steps.append("그 사업의 문서 조각에서 맞는 것이 없어 사업 문서 전체에서 찾는다")
            lex = grant_lex.rank(db, query, None, user, depts=depts) if query else []
            den = dense_ids(question, None, scope_docs=None, user=user, db=db, depts=depts)
        merged = rrf_merge(lex[:TOP_K], den, w_a=0.4, w_b=1.0) if den else lex
        sec = section_hits(db, question, scope_docs)
        if sec:
            # 그래프 축 — 질문에 묶어 적은 절 이름이 있으면 범위 안 문서에서 그 제목의 절(그래프 절 노드)을 바로 후보 앞에
            steps.append(f"[그래프 절] 「{_QUOTED.findall(question)[0][:30]}」 제목의 절 조각 {len(sec)}개를 먼저")
            merged = sec + [c for c in merged if c not in set(sec)]
        by_id = {c["id"]: c for c in chunks_by_ids(db, merged[: k * 6])}
        pool = f"색인 {have}개"
    else:
        # 색인이 덜 찼으면 옛 방식(조각 전체) — 색인 동기화가 따라잡는 동안만
        chunks = corpus(db, scope_docs, user, depts)
        if not chunks and scope_docs is not None:
            steps.append("그 사업의 문서 조각이 없어 사업 문서 전체에서 찾는다")
            chunks = corpus(db, None, user, depts)
        allowed = {c["id"] for c in chunks}
        lex = _lexical_ids(query, chunks, 1) if query else []
        den = dense_ids(question, allowed)
        merged = rrf_merge(lex[:TOP_K], den, w_a=0.4, w_b=1.0) if den else lex
        by_id = {c["id"]: c for c in chunks}
        pool = f"{len(chunks)}개"
    hits = []
    cands = [by_id[c] for c in merged[: k * 6] if c in by_id]
    rerank_on = os.environ.get("ZZAIMY_GRANT_RERANK", "0") == "1" and len(cands) > 1
    # 이웃 확장은 한 번의 쿼리로 — 재순위를 쓰면 후보 전체, 아니면 위쪽만(후보마다 따로 물으면 질의당 수 초, 10/5 실측 5.7초)
    expand_many(db, cands if rerank_on else cands[: k * 2])
    cands = quality_filter(cands)
    if rerank_on:
        # 재순위(Rerank v1, 토르) — 문서 이름을 제목으로 붙여 묻는다. 켜기는 173 대결 수치로(규칙: 채택은 대결 수치로)
        from zzaimy.app.rerank import rerank_chunks
        for c in cands:
            c.setdefault("reg_title", c.get("filename") or "")
        cands = rerank_chunks(question, cands)
        steps.append("[재순위] 후보 조각을 재순위 모델로 다시 정렬")
    order = rerank_hits(question, cands)
    per_doc: dict[int, int] = {}
    for c in order:
        cid = c["id"]
        if per_doc.get(c["doc_id"], 0) >= PER_DOC:
            continue                                     # 한 문서가 상위를 다 차지하지 않게(10/5 실측: 상위 5개 중 3개가 한 문서)
        per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], 0) + 1
        hits.append({"chunk_id": cid, "doc_id": c["doc_id"], "content": c["content"][:EXPAND_TO], "filename": c["filename"],
                     "path": path_of.get(c["doc_id"], []) + [c["filename"]]})
        if len(hits) >= k:
            break
    steps.append(f"[3단계: 근거 선택] 사업 문서 조각 {pool} 중 어휘 {len(lex)}·임베딩 {len(den)} 순위를 섞어 {len(hits)}개")
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


def _build_increment_pg(db, batch: int, limit: int) -> dict:
    """pgvector 표(grant_vec)만 보고 색인을 따라잡는다(ADR-0057) — 표에 없는 살아 있는 조각을 임베딩해 넣고, 사라진 조각은 뺀다.
    배열 파일(npz)은 쓰지 않는다. 화면·점검이 읽는 메타(grant_embeddings.json)는 그대로 적는다."""
    with db._conn() as conn:
        live = {int(r[0]) for r in conn.execute(
            "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?)", TEXT_KINDS).fetchall()}
        have = {int(r[0]) for r in conn.execute("SELECT chunk_id FROM grant_vec").fetchall()}
    todo = sorted(live - have)
    gone = sorted(have - live)
    removed = pg_drop(db, gone)
    added = failed = 0
    new = _fetch(db, todo[:limit])
    for i in range(0, len(new), batch):
        part = new[i:i + batch]
        got = _encode_batch([f"{c['filename']}\n{c['content'][:1200]}" for c in part])
        keep = [(c, v) for c, v in zip(part, got) if v is not None]
        failed += len(part) - len(keep)
        if not keep:
            if failed >= batch * 3:
                raise RuntimeError("임베딩 서비스 없음")
            continue
        added += pg_put(db, [(c["id"], c["doc_id"], x) for c, x in keep])
    total = len(have) - removed + added
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.with_suffix(".json").write_text(json.dumps({"n_chunks": total, "added": added, "removed": removed, "backend": "pgvector"},
                                                     ensure_ascii=False), encoding="utf-8")
    return {"added": added, "removed": removed, "total": total, "pending": max(0, len(todo) - limit)}


def _build_increment(db, batch: int, limit: int) -> dict:
    import os

    import numpy as np
    with db._conn() as conn:
        pg = _pg_ready(conn)
    if pg and os.environ.get("ZZAIMY_DENSE_BACKEND", "pgvector") == "pgvector":
        return _build_increment_pg(db, batch, limit)
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
    if keep_mask is not None and keep_mask.all():
        keep_mask = None                                   # 빠진 조각이 없으면 걸러 낸 사본을 만들지 않는다
    new_ids: list[np.ndarray] = []
    new_vecs: list[np.ndarray] = []
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
        new_vecs.append(np.asarray([x for _c, x in keep], dtype=np.float32))
        new_ids.append(np.array([c["id"] for c, _x in keep], dtype=np.int64))
        pg_put(db, [(c["id"], c["doc_id"], x) for c, x in keep])          # pgvector 표에도 바로(ADR-0057)
        added += len(keep)
    # 남길 것과 새것을 한 번에 한 배열로 — 묶음마다 vstack 하면 색인 크기(5GB)만 한 사본이 묶음 수만큼 생겼다 지워진다
    n_keep = int(keep_mask.sum()) if keep_mask is not None else (len(ids) if ids is not None else 0)
    dim = (vecs.shape[1] if vecs is not None and vecs.ndim == 2 and len(vecs) else
           (new_vecs[0].shape[1] if new_vecs else 0))
    out_ids = np.concatenate([(ids[keep_mask] if keep_mask is not None else ids) if ids is not None
                              else np.zeros((0,), dtype=np.int64), *new_ids]) if (added or n_keep) else np.zeros((0,), dtype=np.int64)
    out_vecs = None
    if dim and (added or (keep_mask is not None and n_keep != len(ids))):
        out_vecs = np.empty((n_keep + added, dim), dtype=np.float32)
        if n_keep:
            if keep_mask is not None:
                np.compress(keep_mask, vecs, axis=0, out=out_vecs[:n_keep])
            else:
                out_vecs[:n_keep] = vecs
        pos = n_keep
        for v in new_vecs:
            out_vecs[pos:pos + len(v)] = v
            pos += len(v)
        new_vecs.clear()
    removed = (len(ids) - n_keep) if ids is not None else 0
    if removed and keep_mask is not None:
        pg_drop(db, [int(i) for i in ids[~keep_mask]])                 # 지워진 조각은 pgvector 표에서도
    if added or removed:
        tmp = INDEX.with_name(f".{INDEX.stem}.{os.getpid()}.tmp.npz")
        # 압축하지 않는다 — 벡터는 거의 줄지 않고, 수 GB 를 회차마다 압축하는 게 색인 속도를 깎았다(10/2 실측 초당 20여 조각)
        np.savez(tmp, ids=out_ids, vectors=out_vecs if out_vecs is not None else np.zeros((0, 1)))
        out_vecs = None
        ids = vecs = None
        _cache.update(mtime=None, ids=None, vecs=None)
        try:
            _check(tmp)                                    # 검사한 뒤에만 바꾼다(본체를 다시 읽지 않고)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if INDEX.exists():
            try:
                _check(INDEX)
                INDEX.replace(PREV)                        # 직전 정상본을 남긴다
            except Exception:
                pass                                       # 깨진 것은 정상본으로 남기지 않는다
        tmp.replace(INDEX)
        (INDEX.with_suffix(".json")).write_text(json.dumps({"n_chunks": int(len(out_ids)), "added": added, "removed": removed},
                                                           ensure_ascii=False), encoding="utf-8")
    return {"added": added, "removed": removed, "total": int(len(out_ids)), "pending": max(0, len(todo) - limit)}


def _pg_ready(conn) -> bool:
    if getattr(conn, "dialect", "") != "postgres":
        return False
    return bool(conn.execute("SELECT to_regclass('grant_vec')").fetchone()[0])


def pg_put(db, rows: list[tuple]) -> int:
    """(조각 번호, 문서 번호, 벡터) 들을 pgvector 표(grant_vec)에 넣는다 — 이미 있으면 바꾼다. 표가 없으면 아무것도 하지 않는다."""
    if not rows:
        return 0
    try:
        with db._conn() as conn:
            if not _pg_ready(conn):
                return 0
            conn.raw.execute("SET statement_timeout = 0")
            vals = [(int(c), int(d) if d is not None else None, "[" + ",".join(f"{float(x):.5f}" for x in v) + "]") for c, d, v in rows]
            conn.executemany("INSERT INTO grant_vec (chunk_id, doc_id, emb) VALUES (?, ?, ?::halfvec)"
                             " ON CONFLICT (chunk_id) DO UPDATE SET doc_id = excluded.doc_id, emb = excluded.emb", vals)
        return len(rows)
    except Exception as e:
        log.warning("pgvector 표 넣기 실패(%s) — 다음 맞추기(178 --load)에서 채운다", type(e).__name__)
        return 0


def pg_drop(db, chunk_ids: list[int]) -> int:
    if not chunk_ids:
        return 0
    try:
        with db._conn() as conn:
            if not _pg_ready(conn):
                return 0
            for i in range(0, len(chunk_ids), 5000):
                part = chunk_ids[i:i + 5000]
                conn.execute(f"DELETE FROM grant_vec WHERE chunk_id IN ({','.join('?' * len(part))})", part)
        return len(chunk_ids)
    except Exception as e:
        log.warning("pgvector 표 지우기 실패(%s)", type(e).__name__)
        return 0


def _fetch(db, chunk_ids: list[int]) -> list[dict]:
    """조각 번호 → 색인용 글(문서 이름 포함)."""
    out = []
    with db._conn() as conn:
        for i in range(0, len(chunk_ids), 500):
            part = chunk_ids[i:i + 500]
            rows = conn.execute(
                "SELECT c.id, c.kind, c.content, d.filename, c.doc_id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
                f" WHERE c.id IN ({','.join('?' * len(part))})", part).fetchall()
            for r in rows:
                out.append({"id": int(r[0]), "filename": r[3], "content": _text({"kind": r[1], "content": r[2]}), "doc_id": int(r[4])})
    out.sort(key=lambda c: c["id"])
    return out
