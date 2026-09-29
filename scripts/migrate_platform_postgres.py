#!/usr/bin/env python3
"""Stage and verify only. Does not change the running application's database."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from zzaimy.app.pg_migration import copy_to_staging, snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New private backup directory')
    parser.add_argument('--schema', required=True, help='New aura_stage_* schema')
    args = parser.parse_args()
    dsn = os.environ.get('ZZAIMY_MIGRATION_DSN')
    if not dsn:
        parser.error('Set ZZAIMY_MIGRATION_DSN; do not pass credentials in CLI arguments')
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    backup = args.output / 'platform.sqlite3'
    try:
        snapshot(args.source, backup)
        report = copy_to_staging(backup, dsn, args.schema)
        report['snapshot_created_at'] = datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc).isoformat()
        path = args.output / 'verification.json'
        with path.open('x', encoding='utf-8') as output:
            os.chmod(path, 0o600)
            json.dump(report, output, ensure_ascii=False, indent=2)
        print(json.dumps({'verified': True, 'tables': len(report['tables']),
                          'rows': sum(t['rows'] for t in report['tables']),
                          'schema': args.schema, 'runtime_changed': False}))
    except Exception as exc:
        # DB error messages may contain complete document rows or credentials.
        print('Staging failed (' + type(exc).__name__ + '). Runtime unchanged; backup retained.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
