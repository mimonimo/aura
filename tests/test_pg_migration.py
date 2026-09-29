import sqlite3

import pytest

from zzaimy.app.pg_migration import pg_type, quote, row_digest, snapshot, table_digest


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
