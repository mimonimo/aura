"""사업 문서 어휘 색인 — 조각마다 Kiwi 명사를 미리 뽑아 DB 에 둔다(PostgreSQL 은 전문 검색 GIN 색인).

검색 때 조각 133만 개를 본문째 읽어 명사를 다시 뽑던 방식(질의 한 번에 수십 초·웹 서버 메모리에 명사 캐시가 끝없이 쌓임,
10/5 실측)을 대신한다. 점수 규칙은 규정 검색과 같다(regulations.lexical_score — IDF·희귀 명사·초점어).
- 후보: 질의 명사 가운데 하나라도 든 조각(열람 권한·사업 범위는 SQL 에서)
- df: 명사마다 그 명사가 든 조각 수(색인으로 센다), n: 색인된 조각 수
색인 갱신은 임베딩 색인과 같은 주기(170 --index)에서 sync() 로 — 새 조각은 더하고 지워진 조각은 뺀다.
"""
from __future__ import annotations

import re

from zzaimy.app.database_backend import table_names

TEXT_KINDS = ("text", "table", "image_text")
_TERM = re.compile(r"^[0-9A-Za-z가-힣]+$")
CANDIDATE_LIMIT = 4000


def _pg(conn) -> bool:
    return getattr(conn, "dialect", "") == "postgres"


def ensure(db) -> None:
    with db._conn() as conn:
        if "grant_lex" in table_names(conn):
            return
        conn.execute("CREATE TABLE IF NOT EXISTS grant_lex (chunk_id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, nouns TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_grant_lex_doc ON grant_lex (doc_id)")
        if _pg(conn):
            conn.execute("ALTER TABLE grant_lex ADD COLUMN IF NOT EXISTS tsv tsvector "
                         "GENERATED ALWAYS AS (to_tsvector('simple', nouns)) STORED")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_grant_lex_tsv ON grant_lex USING GIN (tsv)")


def sync(db, limit: int = 20000) -> dict:
    """색인되지 않은 사업 문서 조각의 명사를 넣고, 문서함에서 사라진 조각은 뺀다. {added, removed, pending}."""
    from zzaimy.app.grant_search import _text
    from zzaimy.app.regulations import extract_nouns
    ensure(db)
    with db._conn() as conn:
        removed = conn.execute(
            "DELETE FROM grant_lex WHERE chunk_id NOT IN (SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?))", TEXT_KINDS).rowcount or 0
        todo = [int(r[0]) for r in conn.execute(
            "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.doc_id LEFT JOIN grant_lex g ON g.chunk_id = c.id"
            " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN (?, ?, ?) AND g.chunk_id IS NULL"
            " ORDER BY c.id", TEXT_KINDS).fetchall()]
    added = 0
    for i in range(0, min(len(todo), limit), 500):
        part = todo[i:i + 500]
        with db._conn() as conn:
            rows = conn.execute(f"SELECT id, doc_id, kind, content FROM doc_chunks WHERE id IN ({','.join('?' * len(part))})",
                                part).fetchall()
            vals = []
            for r in rows:
                nouns = extract_nouns(_text({"kind": r[2], "content": r[3]}))
                vals.append((int(r[0]), int(r[1]), " ".join(sorted(t for t in nouns if _TERM.match(t)))))
            conn.executemany("INSERT INTO grant_lex (chunk_id, doc_id, nouns) VALUES (?, ?, ?) ON CONFLICT (chunk_id) DO NOTHING", vals)
        added += len(vals)
    return {"added": added, "removed": removed, "pending": max(0, len(todo) - limit)}


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


def _access_sql(user: str | None) -> tuple[str, list]:
    if user is None:
        return "", []
    return " AND (COALESCE(d.access_level, 'public') = 'public' OR COALESCE(d.owner, '') = ?)", [user]


def rank(db, query: frozenset[str], scope_docs: set[int] | None = None, user: str | None = None,
         min_overlap: int = 1, limit: int = CANDIDATE_LIMIT) -> list[int]:
    """질의 명사 → 조각 id 순위(규정 검색과 같은 점수 규칙). 범위(scope_docs)·열람 권한은 후보를 뽑을 때 SQL 로 자른다."""
    from zzaimy.app.regulations import lexical_score
    terms = sorted(t for t in query if _TERM.match(t))
    if not terms:
        return []
    acc, acc_args = _access_sql(user)
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
