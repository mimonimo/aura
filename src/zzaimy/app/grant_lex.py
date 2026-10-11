"""사업 문서 어휘 색인 — 조각마다 Kiwi 명사를 미리 뽑아 DB 에 둔다(PostgreSQL 은 전문 검색 GIN 색인).

검색 때마다 조각 본문(133만 개)을 읽어 명사를 다시 뽑으면 질의 한 번에 수십 초가 걸리고 웹 서버 메모리에 명사 캐시가
끝없이 쌓인다. 그래서 미리 뽑아 둔다. 점수 규칙은 규정 검색과 같다(regulations.lexical_score — IDF·희귀 명사·초점어).
- 후보: 질의 명사 가운데 하나라도 든 조각(열람 권한·사업 범위는 SQL 에서)
- df: 명사마다 그 명사가 든 조각 수(색인으로 센다), n: 색인된 조각 수
색인 갱신은 임베딩 색인과 같은 주기(170 --index)에서 sync() 로 — 새 조각은 더하고 지워진 조각은 뺀다.

낱말은 lex_terms 하나로 뽑는다(색인과 질의 모두). Kiwi 가 띄어 쓴 꼴로 내는 복합 명사(「부트 캠프」)는 붙여 쓰고,
영문 약칭(RISE·AID·LINC)은 명사 태그가 아니어도 넣고, 전각 글자(ＡＩＤ)는 반각으로, 영문은 소문자로 맞춘다.
예전 규칙으로 뽑은 줄(v < LEX_VERSION)은 sync() 가 남는 몫만큼 다시 뽑는다.
"""
from __future__ import annotations

import re

from zzaimy.app.database_backend import table_names

TEXT_KINDS = ("text", "table", "image_text")
_TERM = re.compile(r"^[0-9A-Za-z가-힣]+$")
CANDIDATE_LIMIT = 4000
LEX_VERSION = 2


def lex_terms(text: str) -> frozenset[str]:
    """색인·질의 공용 낱말 — 명사(띄어 쓴 복합 명사는 붙여)와 두 글자 이상 영문 낱말, 소문자. 한 낱말 질의도 그대로 찾힌다."""
    from zzaimy.app.regulations import _get_kiwi, extract_nouns
    from zzaimy.app.responder import normalize_input

    t = normalize_input(text or "")[:4000]
    try:
        toks = [(x.form, x.tag) for x in _get_kiwi().tokenize(t)]
        raw = {f for f, tag in toks if tag.startswith("NN") or tag == "SL"}
    except Exception:
        raw = set(extract_nouns(t))
    out = {re.sub(r"\s+", "", f).lower() for f in raw}
    return frozenset(w for w in out if len(w) >= 2 and _TERM.match(w))


def _pg(conn) -> bool:
    return getattr(conn, "dialect", "") == "postgres"


def _has_version(conn) -> bool:
    if _pg(conn):
        return bool(conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()"
                                 " AND table_name = 'grant_lex' AND column_name = 'v'").fetchone())
    return any(r[1] == "v" for r in conn.execute("PRAGMA table_info(grant_lex)").fetchall())


def ensure(db) -> None:
    with db._conn() as conn:
        if "grant_lex" in table_names(conn):
            if not _has_version(conn):          # 낱말 규칙 판 — 옛 줄은 1, 다시 뽑으면 LEX_VERSION
                conn.execute("ALTER TABLE grant_lex ADD COLUMN v INTEGER NOT NULL DEFAULT 1")
            return
        conn.execute("CREATE TABLE IF NOT EXISTS grant_lex (chunk_id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL,"
                     f" nouns TEXT NOT NULL, v INTEGER NOT NULL DEFAULT {LEX_VERSION})")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_grant_lex_doc ON grant_lex (doc_id)")
        if _pg(conn):
            conn.execute("ALTER TABLE grant_lex ADD COLUMN IF NOT EXISTS tsv tsvector "
                         "GENERATED ALWAYS AS (to_tsvector('simple', nouns)) STORED")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_grant_lex_tsv ON grant_lex USING GIN (tsv)")


def sync(db, limit: int = 20000) -> dict:
    """색인되지 않은 사업 문서 조각의 명사를 넣고, 문서함에서 사라진 조각은 뺀다. {added, removed, pending}."""
    from zzaimy.app.grant_search import _text
    ensure(db)
    # 번호만 가져와 차집합으로 — 133만 줄에 NOT IN·LEFT JOIN 을 걸면 쿼리 제한 시간(statement timeout)에 걸린다
    with db._conn() as conn:
        live = {int(r[0]) for r in conn.execute(
            "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?)", TEXT_KINDS).fetchall()}
        have = {int(r[0]) for r in conn.execute("SELECT chunk_id FROM grant_lex").fetchall()}
    gone = sorted(have - live)
    removed = 0
    for i in range(0, len(gone), 1000):
        part = gone[i:i + 1000]
        with db._conn() as conn:
            conn.execute(f"DELETE FROM grant_lex WHERE chunk_id IN ({','.join('?' * len(part))})", part)
        removed += len(part)
    todo = sorted(live - have)
    added = 0
    for i in range(0, min(len(todo), limit), 500):
        part = todo[i:i + 500]
        with db._conn() as conn:
            rows = conn.execute(f"SELECT id, doc_id, kind, content FROM doc_chunks WHERE id IN ({','.join('?' * len(part))})",
                                part).fetchall()
            vals = []
            for r in rows:
                vals.append((int(r[0]), int(r[1]), " ".join(sorted(lex_terms(_text({"kind": r[2], "content": r[3]})))), LEX_VERSION))
            conn.executemany("INSERT INTO grant_lex (chunk_id, doc_id, nouns, v) VALUES (?, ?, ?, ?) ON CONFLICT (chunk_id) DO NOTHING", vals)
        added += len(vals)
    # 옛 규칙으로 뽑은 줄은 남는 몫만큼 다시 뽑는다 — 한 번에 다 하지 않고 주기마다 이어 간다
    budget = max(0, limit - added)
    with db._conn() as conn:
        stale = [int(r[0]) for r in conn.execute("SELECT chunk_id FROM grant_lex WHERE v < ? ORDER BY chunk_id LIMIT ?",
                                                 (LEX_VERSION, budget)).fetchall()] if budget else []
        left = int(conn.execute("SELECT COUNT(*) FROM grant_lex WHERE v < ?", (LEX_VERSION,)).fetchone()[0])
    refreshed = 0
    for i in range(0, len(stale), 500):
        part = stale[i:i + 500]
        with db._conn() as conn:
            rows = conn.execute(f"SELECT id, kind, content FROM doc_chunks WHERE id IN ({','.join('?' * len(part))})",
                                part).fetchall()
            conn.executemany("UPDATE grant_lex SET nouns = ?, v = ? WHERE chunk_id = ?",
                             [(" ".join(sorted(lex_terms(_text({"kind": r[1], "content": r[2]})))), LEX_VERSION, int(r[0]))
                              for r in rows])
        refreshed += len(part)
    _CACHE.clear()
    return {"added": added, "removed": removed, "pending": max(0, len(todo) - limit),
            "refreshed": refreshed, "stale": max(0, left - refreshed)}


_CACHE: dict = {}
TTL = 600


def _cached(key, fn):
    import time
    got = _CACHE.get(key)
    if got and time.time() - got[0] < TTL:
        return got[1]
    val = fn()
    _CACHE[key] = (time.time(), val)
    return val


def coverage(db) -> tuple[int, int]:
    """(색인된 조각 수, 색인 대상 조각 수) — 색인이 덜 찼으면 검색이 옛 방식으로 물러난다. 10분 캐시(질의마다 133만 줄을 세지 않게)."""
    return _cached(("coverage", str(getattr(db, "path", ""))), lambda: _coverage(db))


def _coverage(db) -> tuple[int, int]:
    with db._conn() as conn:
        if "grant_lex" not in table_names(conn):
            return 0, 1
        have = conn.execute("SELECT COUNT(*) FROM grant_lex").fetchone()[0]
        want = conn.execute("SELECT COUNT(*) FROM doc_chunks c JOIN documents d ON d.id = c.doc_id WHERE d.doc_type = 'grant'"
                            " AND d.status = 'reviewed' AND c.kind IN (?, ?, ?)", TEXT_KINDS).fetchone()[0]
    return int(have or 0), int(want or 0)


def _access_sql(user: str | None, depts: list[str] | None = None) -> tuple[str, list]:
    from zzaimy.app.grant_search import _access
    return _access(user, depts)


def rank(db, query: frozenset[str], scope_docs: set[int] | None = None, user: str | None = None,
         min_overlap: int = 1, limit: int = CANDIDATE_LIMIT, depts: list[str] | None = None) -> list[int]:
    """질의 명사 → 조각 id 순위(규정 검색과 같은 점수 규칙). 범위(scope_docs)·열람 권한은 후보를 뽑을 때 SQL 로 자른다."""
    from zzaimy.app.regulations import lexical_score
    # 질의는 lex_terms 로 뽑아 넘기는 것이 맞다. extract_nouns 결과가 와도 같은 꼴(붙여 쓰기·소문자)로 맞춘다
    terms = sorted({w for w in (re.sub(r"\s+", "", t).lower() for t in query) if _TERM.match(w)})
    if not terms:
        return []
    acc, acc_args = _access_sql(user, depts)
    scope_sql, scope_args = "", []
    if scope_docs is not None:
        if not scope_docs:
            return []
        ids = sorted(scope_docs)
        scope_sql = f" AND g.doc_id IN ({','.join('?' * len(ids))})"
        scope_args = ids
    with db._conn() as conn:
        if _pg(conn):
            key = str(getattr(db, "path", ""))
            n = _cached(("n", key), lambda: int(conn.execute("SELECT COUNT(*) FROM grant_lex").fetchone()[0]))
            df = {t: _cached(("df", key, t), lambda t=t: int(conn.execute(
                "SELECT COUNT(*) FROM grant_lex WHERE tsv @@ to_tsquery('simple', ?)", (t,)).fetchone()[0])) for t in terms}
            # 후보는 드문 명사부터, 그 명사들이 든 조각 합이 5만을 넘지 않을 만큼만(흔한 명사로 수십만 줄을 읽지 않게) — 점수는 전체 명사로
            pick, total = [], 0
            for t in sorted((t for t in terms if df[t]), key=lambda t: df[t]):
                if pick and total + df[t] > 50000:
                    break
                pick.append(t)
                total += df[t]
            if not pick:
                return []
            tsq = " | ".join(pick)
            rows = conn.execute(
                "SELECT g.chunk_id, g.nouns FROM grant_lex g JOIN documents d ON d.id = g.doc_id"
                " WHERE g.tsv @@ to_tsquery('simple', ?)" + scope_sql + acc + " LIMIT ?",
                (tsq, *scope_args, *acc_args, max(limit, 20000))).fetchall()
        else:                                             # 시험용 SQLite — 색인 없이 훑는다(작은 DB)
            all_rows = conn.execute("SELECT g.chunk_id, g.nouns FROM grant_lex g JOIN documents d ON d.id = g.doc_id"
                                    " WHERE 1=1" + scope_sql + acc, (*scope_args, *acc_args)).fetchall()
            n = conn.execute("SELECT COUNT(*) FROM grant_lex").fetchone()[0]
            sets = [(int(r[0]), frozenset(str(r[1]).split())) for r in all_rows]
            df = {t: sum(1 for _i, s in sets if t in s) for t in terms}
            rows = [(i, " ".join(s)) for i, s in sets if s & set(terms)][:limit]
    nouns = {int(r[0]): frozenset(str(r[1]).split()) for r in rows}
    return lexical_score(frozenset(terms), nouns, df, int(n or 0), min_overlap)
