"""Preserve old SQLite but make legacy direct access fail, not read stale data.

Run only after final migration and PostgreSQL health checks. No file is deleted.
"""
from pathlib import Path
import os
import sqlite3

from zzaimy.app.database_backend import connect


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / 'data/platform/platform.db'
    retired = source.with_name('platform.retired-c129.sqlite3')
    report = root / 'data/platform/backup/pg-final-c129/verification.json'
    import json
    if not json.loads(report.read_text())['verified'] or retired.exists():
        raise RuntimeError('Verified migration and unused retirement path required')
    with connect(source, readonly=True) as pg:
        if getattr(pg, 'dialect', '') != 'postgres':
            raise RuntimeError('PostgreSQL must already be active')
        pg.execute('SELECT count(*) FROM documents').fetchone()
    with sqlite3.connect(source, timeout=10) as old:
        busy, _, _ = old.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if busy:
            raise RuntimeError('SQLite is still in use')
        old.execute('BEGIN EXCLUSIVE')
        old.rollback()
    old.close()
    os.rename(source, retired)
    # Any legacy sqlite3.connect(path) must fail at its first SQL statement.
    fd = os.open(source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as marker:
        marker.write('Platform data moved to PostgreSQL. Use zzaimy.app.database_backend.connect.\n')
    print('Old SQLite preserved; legacy direct SQLite access now fails closed.')


if __name__ == '__main__':
    main()
