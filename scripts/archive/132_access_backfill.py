#!/usr/bin/env python3
"""열람 등급 소급 — 새 열(access_level)이 생긴 뒤 기존 문서·조각에 기본 규칙(access_policy)을 적용한다.

규칙: 기준 문서(regulation)·공개 수집분(owner=corpus)은 public, 그 밖의 접수 문서는 dept.
이미 public 이 아닌 값이 든 문서(사람이 정한 것)는 건드리지 않는다. 조각은 문서의 값을 물려받는다.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/132_access_backfill.py [--db data/platform/platform.db] [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app.access_policy import default_level  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    Database(Path(args.db))                       # 마이그레이션(열 추가)만 보장
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, doc_type, owner, dept, access_level FROM documents").fetchall()
    plan: list[tuple[int, str]] = []
    for r in rows:
        want = default_level(r["doc_type"], r["owner"])
        if (r["access_level"] or "public") == "public" and want != "public":
            plan.append((r["id"], want))
    print(f"문서 {len(rows)}건 · 등급 바꿀 것 {len(plan)}건 " + str(Counter(w for _, w in plan)))
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply")
        return 0
    for did, lvl in plan:
        conn.execute("UPDATE documents SET access_level = ? WHERE id = ?", (lvl, did))
    # 조각은 문서의 값을 물려받는다(부서도 함께 맞춘다)
    conn.execute("UPDATE regulation_chunks SET access_level = (SELECT access_level FROM documents d WHERE d.id = regulation_chunks.doc_id)"
                 " WHERE doc_id IN (SELECT id FROM documents)")
    conn.execute("UPDATE regulation_chunks SET dept = (SELECT dept FROM documents d WHERE d.id = regulation_chunks.doc_id)"
                 " WHERE doc_id IN (SELECT id FROM documents)")
    conn.commit()
    lv = conn.execute("SELECT access_level, COUNT(*) FROM documents GROUP BY 1").fetchall()
    ch = conn.execute("SELECT access_level, COUNT(*) FROM regulation_chunks GROUP BY 1").fetchall()
    print("문서 등급:", [tuple(r) for r in lv], "· 조각 등급:", [tuple(r) for r in ch])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
