"""Non-destructive SQLite snapshot → PostgreSQL staging copy.

Not a runtime backend or a cutover tool. Defaults, indexes and foreign keys
require the separately reviewed runtime schema before switching the app.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def snapshot(source: Path, destination: Path) -> None:
    """SQLite backup API includes committed WAL data; never overwrite a backup."""
    if not source.is_file():
        raise ValueError('SQLite source does not exist')
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite integrity check failed')


def pg_type(declared: str) -> str:
    types = {'INTEGER': 'BIGINT', 'TEXT': 'TEXT', 'REAL': 'DOUBLE PRECISION',
             'BLOB': 'BYTEA'}
    if declared.upper() not in types:
        raise ValueError('Unsupported SQLite column type: ' + declared)
    return types[declared.upper()]


def prepare_runtime_snapshot(source: Path, destination: Path) -> dict:
    """Preserve source; remove only approved orphan relationships from a new copy.

    Unexpected violations abort the transaction. No document, chat, or file is
    removed. The untouched source snapshot remains the recovery artifact.
    """
    snapshot(source, destination)
    allowed = {('mask_events', 'documents'), ('doc_entities', 'documents'),
               ('doc_entities', 'entities')}
    with closing(sqlite3.connect(destination)) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            violations = db.execute('PRAGMA foreign_key_check').fetchall()
            if any((table, parent) not in allowed or rowid is None
                   for table, rowid, parent, _ in violations):
                raise ValueError('Unexpected foreign key violation; no cleanup applied')
            affected = {}
            for table, rowid, _, _ in violations:
                affected.setdefault(table, set()).add(rowid)
            for table, rowids in affected.items():
                db.executemany(f'DELETE FROM {quote(table)} WHERE rowid=?',
                               [(rowid,) for rowid in sorted(rowids)])
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Foreign key violations remain; cleanup rolled back')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Integrity check failed; cleanup rolled back')
    return {'removed': {table: len(ids) for table, ids in sorted(affected.items())},
            'foreign_key_check': 'ok', 'source_changed': False}


def row_digest(row) -> str:
    """Typed digest; preserves NULL vs empty, integers vs strings, and blobs."""
    cells = []
    for value in row:
        if isinstance(value, (bytes, memoryview)):
            cells.append(['bytes', bytes(value).hex()])
        elif value is None:
            cells.append(['null', None])
        elif isinstance(value, int):
            cells.append(['int', value])
        elif isinstance(value, float):
            cells.append(['float', value.hex()])
        elif isinstance(value, str):
            if '\x00' in value:
                raise ValueError('NUL text cannot be preserved in PostgreSQL')
            cells.append(['text', value])
        else:
            raise ValueError('Unsupported stored value type')
    return hashlib.sha256(json.dumps(cells, ensure_ascii=False).encode()).hexdigest()


def table_digest(rows) -> tuple[int, str]:
    # Sorting row hashes avoids SQLite/PostgreSQL text collation differences.
    counts = Counter(row_digest(row) for row in rows)
    digest = hashlib.sha256()
    for item, count in sorted(counts.items()):
        digest.update(f'{item}:{count}\n'.encode())
    return sum(counts.values()), digest.hexdigest()


def copy_to_staging(source: Path, dsn: str, schema: str) -> dict:
    """Copy all user tables in a single PG transaction, compare every value.

    Reject views/triggers instead of silently losing their semantics. Existing
    schemas always fail. Never prints data, connection strings, or row values.
    """
    import psycopg
    from psycopg import sql

    if not re.fullmatch(r'aura_stage_[a-z0-9_]{1,40}', schema):
        raise ValueError('Use a new aura_stage_* schema (lowercase letters/digits)')
    if not source.is_file():
        raise ValueError('Snapshot does not exist')
    report = {'schema': schema, 'purpose': 'staging-only', 'tables': [], 'verified': False}
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        # Hold one read snapshot, even if a caller mistakenly supplies a live DB.
        src.execute('BEGIN')
        if src.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('view','trigger')").fetchone()[0]:
            raise ValueError('Views/triggers require explicit migration')
        tables = src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        with psycopg.connect(dsn, connect_timeout=10) as pg:
            pg.execute("SET LOCAL lock_timeout = '5s'")
            pg.execute("SET LOCAL statement_timeout = '120s'")
            pg.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            for (name,) in tables:
                columns = src.execute(f'PRAGMA table_xinfo({quote(name)})').fetchall()
                if any(c[6] for c in columns):
                    raise ValueError('Generated/hidden columns require explicit migration')
                fields = [c[1] for c in columns]
                definitions = [sql.SQL('{} {}{}').format(sql.Identifier(c[1]),
                    sql.SQL(pg_type(c[2])), sql.SQL(' NOT NULL' if c[3] else '')) for c in columns]
                keys = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
                if keys:
                    definitions.append(sql.SQL('PRIMARY KEY ({})').format(sql.SQL(',').join(map(sql.Identifier, keys))))
                target = sql.Identifier(schema, name)
                pg.execute(sql.SQL('CREATE TABLE {} ({})').format(target, sql.SQL(',').join(definitions)))
                select = f'SELECT {",".join(map(quote, fields))} FROM {quote(name)}'
                expected = table_digest(src.execute(select))
                with pg.cursor().copy(sql.SQL('COPY {} ({}) FROM STDIN').format(
                        target, sql.SQL(',').join(map(sql.Identifier, fields)))) as writer:
                    for row in src.execute(select):
                        writer.write_row(row)
                with pg.cursor(name='verify_migration') as reader:
                    reader.execute(sql.SQL('SELECT {} FROM {}').format(sql.SQL(',').join(map(sql.Identifier, fields)), target))
                    actual = table_digest(reader)
                if actual != expected:
                    raise ValueError('Value/count verification failed for table ' + name)
                report['tables'].append({'table': name, 'rows': expected[0], 'sha256': expected[1]})
            report['verified'] = True
    return report
