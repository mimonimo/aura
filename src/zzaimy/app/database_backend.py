"""Explicit platform backend selection; unrelated corpus SQLite stays local.

SQL remains parameterised. Only placeholders and our schema's identity syntax
are adapted here; application-specific upserts/locking are explicit in callers.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import threading


_ENV_LOCAL = Path(__file__).resolve().parents[3] / '.env.local'
_KEYS = ('ZZAIMY_DATABASE_URL', 'ZZAIMY_PLATFORM_SQLITE_PATH', 'ZZAIMY_DATABASE_SCHEMA')
_CONFIG_CACHE: dict = {}


def postgres_config(path):
    """연결 설정 — 같은 경로·환경·.env.local 이면 한 번 읽은 값을 쓴다(연결마다 파일을 다시 읽어 7ms 씩 들었다, 10-28 화면 측정)."""
    try:
        mtime = _ENV_LOCAL.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = (str(path), os.getcwd(), mtime, tuple(os.environ.get(k) for k in _KEYS))
    if key not in _CONFIG_CACHE:
        if len(_CONFIG_CACHE) > 64:
            _CONFIG_CACHE.clear()
        _CONFIG_CACHE[key] = _postgres_config(path)
    return _CONFIG_CACHE[key]


def _postgres_config(path):
    # CLI/index jobs must select the same backend as systemd, even when their
    # shell did not export EnvironmentFile. Read only DB keys, never mutate env.
    from dotenv import dotenv_values
    local = dotenv_values(_ENV_LOCAL)
    def setting(key, default=''):
        return os.environ.get(key, local.get(key) or default)
    dsn = setting('ZZAIMY_DATABASE_URL')
    selected = Path(setting('ZZAIMY_PLATFORM_SQLITE_PATH', 'data/platform/platform.db'))
    if not dsn or Path(path).resolve() != selected.resolve():
        return None
    schema = setting('ZZAIMY_DATABASE_SCHEMA')
    if not re.fullmatch(r'aura_(?:app|test)_[a-z0-9_]{1,40}', schema):
        raise ValueError('An explicit aura_app_* or aura_test_* runtime schema is required')
    return dsn, schema


def placeholders(statement: str) -> str:
    """Convert qmarks outside SQL literals/comments, retaining literal ? and %."""
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/)", statement)
    return ''.join(part.replace('%', '%%') if i % 2 else part.replace('%', '%%').replace('?', '%s')
                   for i, part in enumerate(parts))


def _positions(keys) -> dict:
    pos: dict = {}
    for i, k in enumerate(keys):
        pos.setdefault(k, i)                               # 같은 이름이 둘이면 앞의 것(list.index 와 같게)
    return pos


class Row:
    __slots__ = ("_keys", "_pos", "_values")

    def __init__(self, keys, values, pos=None):
        self._keys, self._values = keys, values
        self._pos = pos if pos is not None else _positions(keys)

    def keys(self):
        return self._keys

    def __getitem__(self, key):
        # 열 이름 → 자리는 커서마다 한 번 만든 사전으로 찾는다(list.index 는 큰 조회에서 매 칸마다 목록을 훑는다)
        return self._values[self._pos[key]] if isinstance(key, str) else self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def _row_factory(cursor):
    keys = [c.name for c in cursor.description] if cursor.description else []
    pos = _positions(keys)
    return lambda values: Row(keys, values, pos)


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor, self.lastrowid = cursor, lastrowid

    def __getattr__(self, key):
        return getattr(self._cursor, key)

    def __iter__(self):
        return iter(self._cursor)


_IDENTITY_TABLES = frozenset(('documents', 'reviews', 'chat_sessions', 'chat_messages',
    'projects', 'project_notes', 'doc_chunks', 'files', 'doc_assets', 'regulation_chunks',
    'entities', 'datasets', 'quality_reports', 'egress_requests', 'draft_history',
    'mask_events', 'chat_sources', 'chat_revisions', 'chunk_questions'))


# 연결 재사용 — 요청 하나가 DB 를 수십 번 부르고, 부를 때마다 새로 연결하면(13ms + 설정 4번) 화면마다 1초 가까이 들었다(10-28).
# 프로세스(pid)·설정(dsn·schema·읽기 전용)마다 쉬는 연결을 몇 개 들고 있다가 내준다. 돌려받을 때 열린 거래는 되돌리고
# 세션 설정(시간 한도)을 처음 값으로 되돌린다 — 앞 사용자가 바꾼 SET 이 다음 사용자에게 새지 않게.
_POOL: dict = {}
_POOL_LOCK = threading.Lock()
POOL_SIZE = int(os.environ.get('ZZAIMY_DB_POOL', '16') or 0)
_RESET = "SET lock_timeout = '10s'; SET statement_timeout = '60s'"


def _pool_take(key):
    with _POOL_LOCK:
        idle = _POOL.get(key)
        while idle:
            raw = idle.pop()
            if not raw.closed:
                return raw
    return None


def _pool_give(key, raw) -> None:
    from psycopg.pq import TransactionStatus
    try:
        if raw.closed:
            return
        if raw.info.transaction_status != TransactionStatus.IDLE:
            raw.rollback()
        auto = raw.autocommit
        raw.autocommit = True
        raw.execute(_RESET)
        raw.autocommit = auto
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        return
    with _POOL_LOCK:
        idle = _POOL.setdefault(key, [])
        if len(idle) < POOL_SIZE:
            idle.append(raw)
            return
    raw.close()


class PostgresConnection:
    dialect = 'postgres'

    def __init__(self, dsn, schema, readonly=False):
        self._key = (os.getpid(), dsn, schema, bool(readonly))
        raw = _pool_take(self._key) if POOL_SIZE > 0 else None
        if raw is not None:
            self.raw = raw
            return
        import psycopg
        from psycopg import sql
        self.raw = psycopg.connect(dsn, connect_timeout=10, autocommit=True, row_factory=_row_factory)
        try:
            if not self.raw.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (schema,)).fetchone():
                raise ValueError('Runtime schema does not exist; provision and verify it first')
            self.raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            self.raw.execute("SET lock_timeout = '10s'")
            self.raw.execute("SET statement_timeout = '60s'")
            if readonly:
                self.raw.execute('SET default_transaction_read_only = on')
            self.raw.autocommit = readonly
        except Exception:
            self.raw.close()
            raise

    def execute(self, statement, parameters=()):
        match = re.match(r'\s*INSERT\s+INTO\s+(\w+)', statement, re.I)
        identity = bool(match and match[1] in _IDENTITY_TABLES and not re.search(r'\bRETURNING\b', statement, re.I))
        if identity:
            statement = statement.rstrip().rstrip(';') + ' RETURNING id'
        cursor = self.raw.execute(placeholders(statement), parameters)
        row = cursor.fetchone() if identity else None
        return Cursor(cursor, row[0] if row else None)

    def executemany(self, statement, parameters):
        cursor = self.raw.cursor()
        cursor.executemany(placeholders(statement), parameters)
        return Cursor(cursor)

    def executescript(self, script):
        # Internal DDL only; no user-supplied scripts.
        script = re.sub(r'--[^\n]*', '', script)
        script = re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
                        'BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY', script, flags=re.I)
        for statement in script.split(';'):
            if statement.strip():
                self.execute(statement)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.raw.commit() if kind is None else self.raw.rollback()
        except Exception:
            self.raw.close()
            raise
        finally:
            self.close()

    def close(self):
        """돌려주기 — 쉬는 연결 묶음에 넣는다(가득 찼거나 상태가 이상하면 닫는다). 두 번 불러도 한 번만."""
        if getattr(self, "_released", False):
            return
        self._released = True
        if POOL_SIZE > 0:
            _pool_give(self._key, self.raw)
        else:
            self.raw.close()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


def connect(path, *, readonly=False):
    config = postgres_config(path)
    if config:
        return PostgresConnection(*config, readonly=readonly)
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10) if readonly else sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn):
    query = "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()" if getattr(conn, 'dialect', '') == 'postgres' else "SELECT name FROM sqlite_master WHERE type='table'"
    return {row[0] for row in conn.execute(query)}


def column_names(conn, table):
    if getattr(conn, 'dialect', '') == 'postgres':
        return {row[0] for row in conn.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?', (table,))}
    if not re.fullmatch(r'[a-z_]+', table):
        raise ValueError('Invalid internal table name')
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def lock_session(conn, session_id):
    if getattr(conn, 'dialect', '') == 'postgres':
        conn.execute('SELECT id FROM chat_sessions WHERE id=? FOR UPDATE', (session_id,)).fetchone()
    else:
        conn.execute('BEGIN IMMEDIATE')


def install_functions(conn):
    """Small explicit SQL compatibility functions, scoped to runtime schema.

    이미 있으면 다시 만들지 않는다 — 연결마다 CREATE OR REPLACE 를 보내면 동시에 연결한 프로세스끼리
    「tuple concurrently updated」로 실패한다."""
    have = {r[0] for r in conn.raw.execute(
        "SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"
        " AND proname IN ('instr', 'json_extract')").fetchall()}
    if {"instr", "json_extract"} <= have:
        return
    conn.raw.execute("""CREATE OR REPLACE FUNCTION instr(text, text) RETURNS integer
        LANGUAGE sql IMMUTABLE STRICT AS 'SELECT strpos($1, $2)'""")
    conn.raw.execute("""CREATE OR REPLACE FUNCTION json_extract(text, text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT AS $$SELECT $1::jsonb ->> substring($2 from 3)$$""")
