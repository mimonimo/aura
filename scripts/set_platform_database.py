#!/usr/bin/env python3
"""Set platform DB keys in an existing environment file (service must be stopped).

DSN comes from ZZAIMY_MIGRATION_DSN; never print secrets. This does not start
the service. Preserve every unrelated setting and back up the previous file.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--schema', required=True)
    ap.add_argument('--backup', required=True, type=Path)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not re.fullmatch(r'aura_app_[a-z0-9_]{1,40}', args.schema):
        raise ValueError('Invalid runtime schema')
    state = subprocess.run(['systemctl', '--user', 'is-active', 'zzaimy.service'], capture_output=True, text=True)
    if state.stdout.strip() not in ('inactive', 'failed'):
        raise RuntimeError('Stop service before changing database configuration')
    dsn = os.environ['ZZAIMY_MIGRATION_DSN']
    if any(c in dsn for c in ('\n', '\r', '"', '\\', '$', '`')):
        raise ValueError('Use a simple libpq DSN/service reference')
    import psycopg
    with psycopg.connect(dsn) as pg:
        if not pg.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (args.schema,)).fetchone():
            raise ValueError('Verified runtime schema required')
    path = root / '.env.local'
    original = path.read_bytes()
    fd = os.open(args.backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as backup:
        backup.write(original)
    values = {'ZZAIMY_DATABASE_URL': dsn, 'ZZAIMY_DATABASE_SCHEMA': args.schema,
              'ZZAIMY_PLATFORM_SQLITE_PATH': str(root / 'data/platform/platform.db')}
    lines = [line for line in original.decode().splitlines()
             if not any(re.match(r'^\s*(?:export\s+)?' + key + r'\s*=', line) for key in values)]
    lines += [key + '="' + value + '"' for key, value in values.items()]
    fd, temporary = tempfile.mkstemp(prefix='.database-env-', dir=root)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write('\n'.join(lines) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Database configuration updated; service remains stopped.')


if __name__ == '__main__':
    main()
