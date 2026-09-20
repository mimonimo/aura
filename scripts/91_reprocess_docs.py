#!/usr/bin/env python3
"""지정한 문서만 다시 처리한다 — 추출기를 고친 뒤 해당 문서에만 소급 적용할 때.

전체 재처리(61)는 몇 시간이 걸리고 모든 문서를 흔든다. 여기서는 id 나 조건으로 고른
문서만 파이프라인에 다시 넣는다. 원본 파일이 없으면 건너뛴다.
무거운 작업 잠금(/tmp/zzaimy-heavy.lock)을 잡는다 — 재색인·재처리와 겹치지 않게.
끝나면 재색인 필요 표시가 남으므로 scripts/66_reindex.sh 를 이어서 돌린다.

사용:
  python scripts/91_reprocess_docs.py --ids 5,6,7
  python scripts/91_reprocess_docs.py --where "doc_type='regulation' AND filename LIKE '%.pdf'" --dry-run
"""
from __future__ import annotations

import argparse
import fcntl
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--where", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.ids and not args.where:
        ap.error("--ids 또는 --where 가 필요합니다")

    db_path = ROOT / "data" / "platform" / "platform.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        rows = conn.execute(
            f"SELECT id, filename, stored_path FROM documents WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id",
            ids).fetchall()
    else:
        rows = conn.execute(
            f"SELECT id, filename, stored_path FROM documents WHERE {args.where} ORDER BY id").fetchall()
    conn.close()
    print(f"대상 {len(rows)}건", flush=True)
    for r in rows:
        print(f"  [{r['id']}] {r['filename']}")
    if args.dry_run or not rows:
        return 0

    lock = open("/tmp/zzaimy-heavy.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("다른 무거운 작업이 실행 중 — 중단", file=sys.stderr)
        return 1

    from zzaimy.app.db import Database
    from zzaimy.app.pipeline import DocumentProcessor

    db = Database(db_path)
    proc = DocumentProcessor()
    ok = failed = missing = 0
    for r in rows:
        p = Path(r["stored_path"])
        if not p.exists():
            missing += 1
            print(f"  [{r['id']}] 원본 없음")
            continue
        t0 = time.time()
        proc.process(db, r["id"], p)
        d = db.get_document(r["id"]) or {}
        state = d.get("status")
        ok += state == "reviewed"
        failed += state == "failed"
        print(f"  [{r['id']}] → {state} · {d.get('parse_note') or ''} ({time.time() - t0:.0f}초)", flush=True)
    print(f"재처리 결과: 성공 {ok} / 실패 {failed} / 원본 없음 {missing}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
