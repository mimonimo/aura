import sqlite3
import os
import uuid

import pytest

from zzaimy.app.pg_migration import copy_to_staging, pg_type, quote, row_digest, snapshot, table_digest


def test_snapshot_includes_wal_and_never_overwrites(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'snapshot.db'
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE data (id INTEGER PRIMARY KEY, text TEXT)')
        db.execute('INSERT INTO data VALUES (7, ?)', ('한글',))
        db.commit()
        snapshot(source, target)
        with sqlite3.connect(target) as copy:
            assert copy.execute('SELECT * FROM data').fetchall() == [(7, '한글')]
        with pytest.raises(FileExistsError):
            snapshot(source, target)
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        snapshot(tmp_path / 'missing.db', tmp_path / 'never.db')


def test_digest_preserves_types_duplicates_and_order_independence():
    rows = [(None, '한글', 1, b'\x00', 1.5), ('', '한글', 1, b'', 1.5)]
    assert table_digest(rows) == table_digest(reversed(rows))
    assert table_digest(rows) != table_digest(rows + rows)
    assert row_digest([1]) != row_digest(['1'])
    assert row_digest([None]) != row_digest([''])
    with pytest.raises(ValueError):
        row_digest(['bad\x00text'])


def test_types_fail_closed():
    assert pg_type('INTEGER') == 'BIGINT'
    assert pg_type('BLOB') == 'BYTEA'
    with pytest.raises(ValueError):
        pg_type('NUMERIC')
    assert quote('unsafe"table') == '"unsafe""table"'


@pytest.fixture
def postgres_stage():
    dsn = os.environ.get('ZZAIMY_TEST_MIGRATION_DSN')
    if not dsn:
        pytest.skip('requires a dedicated PostgreSQL test database')
    psycopg = pytest.importorskip('psycopg')
    schema = 'aura_stage_test_' + uuid.uuid4().hex[:20]
    yield dsn, schema
    # Only the uniquely named schema owned by this test is removed.
    with psycopg.connect(dsn) as db:
        db.execute(psycopg.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(psycopg.sql.Identifier(schema)))


def test_postgres_copy_verifies_every_value_and_refuses_overwrite(tmp_path, postgres_stage):
    import psycopg
    dsn, schema = postgres_stage
    source = tmp_path / 'fixture.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE "odd name" (id INTEGER PRIMARY KEY, text TEXT, data BLOB, score REAL)')
        db.executemany('INSERT INTO "odd name" VALUES (?, ?, ?, ?)',
                       [(4, '한글\n문서', b'\x00\xff', 1.5), (9, None, None, None)])
        db.execute('CREATE TABLE empty (key TEXT PRIMARY KEY, value TEXT)')
    report = copy_to_staging(source, dsn, schema)
    assert report['verified']
    assert sum(t['rows'] for t in report['tables']) == 2
    with pytest.raises(psycopg.errors.DuplicateSchema):
        copy_to_staging(source, dsn, schema)
    with psycopg.connect(dsn) as db:
        rows = db.execute(psycopg.sql.SQL('SELECT * FROM {} ORDER BY id').format(psycopg.sql.Identifier(schema, 'odd name'))).fetchall()
    assert rows == [(4, '한글\n문서', b'\x00\xff', 1.5), (9, None, None, None)]


def test_postgres_failure_rolls_back_whole_schema(tmp_path, postgres_stage):
    import psycopg
    dsn, schema = postgres_stage
    source = tmp_path / 'bad.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE a_good (id INTEGER)')
        db.execute('INSERT INTO a_good VALUES (1)')
        db.execute('CREATE TABLE z_bad (id INTEGER)')
        db.execute("INSERT INTO z_bad VALUES ('not an integer')")
    with pytest.raises(psycopg.Error):
        copy_to_staging(source, dsn, schema)
    with psycopg.connect(dsn) as db:
        assert db.execute('SELECT count(*) FROM pg_namespace WHERE nspname=%s', (schema,)).fetchone()[0] == 0
