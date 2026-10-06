"""서류 갈래 다시 매기기 — 반입 때 붙은 옛 갈래(documents.kind)를 지금 분류기(doc_routing.guess_kind)로 다시 정한다.

2026-10-06 분류기에 일반 서류 종류 6가지(증빙·회계, 교육과정·교과, 회의 자료, 홍보물, 도면, 개인 제출물)를 더했는데,
라이브러리에는 반입 때의 옛 갈래가 남아 「책편집 PPT → 결과보고서」처럼 보였다. 제목과 본문 앞머리 4,000자로 다시 판단한다.
기준 문서(regulation)는 담당자가 정한 갈래라 건드리지 않는다. 판단이 서지 않으면(빈 값) 옛 갈래를 그대로 둔다.
중복·판본 판정이나 조각 삭제는 하지 않는다(그건 136).

실행: env PYTHONPATH=src .venv/bin/python scripts/179_kind_backfill.py            # 바뀔 건수만
      env PYTHONPATH=src .venv/bin/python scripts/179_kind_backfill.py --apply    # 백업 뒤 적용
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_routing import KINDS, guess_kind  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    with db._conn() as conn:
        ids = [int(r[0]) for r in conn.execute("SELECT id FROM documents WHERE doc_type <> 'regulation' ORDER BY id")]
    changes, moves, old_all = [], Counter(), {}
    for k in range(0, len(ids), args.batch):
        part = ids[k:k + args.batch]
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT id, filename, doc_type, kind, substr(COALESCE(masked_text, ''), 1, 4000) FROM documents"
                f" WHERE id IN ({','.join('?' * len(part))})", part).fetchall()
        for r in rows:
            did, name, dtype, old, head = int(r[0]), r[1] or "", r[2], r[3] or "", r[4] or ""
            new, _why = guess_kind(name, head, dtype)
            if new and new != old:
                changes.append((new, did))
                old_all[did] = old
                moves[(KINDS.get(old, old or "미정"), KINDS.get(new, new))] += 1
    print(f"문서 {len(ids):,} · 바뀔 갈래 {len(changes):,}")
    for (a, b), n in moves.most_common(25):
        print(f"  {a:10s} → {b:10s} {n:,}")
    if not args.apply:
        return
    bk = ROOT / "data/platform" / f"kind_backup-{time.strftime('%Y%m%d%H%M%S')}.json"
    bk.write_text(json.dumps(old_all, ensure_ascii=False), encoding="utf-8")
    for k in range(0, len(changes), args.batch):
        with db._conn() as conn:
            conn.executemany("UPDATE documents SET kind = ? WHERE id = ?", changes[k:k + args.batch])
    print(f"적용 {len(changes):,} · 백업 {bk}")


if __name__ == "__main__":
    main()
