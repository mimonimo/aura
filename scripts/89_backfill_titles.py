#!/usr/bin/env python3
"""이미 적재된 문서에 정식 이름·기준 날짜를 채운다.

왜 필요한가. 'law03.pdf' 같은 파일 이름이 규정 조각의 `reg_title`로 저장돼 검색 근거·인용에
그대로 실렸다. 새로 들어오는 문서는 파이프라인이 첫 쪽 이름을 쓰지만(doc_title.py),
이미 들어온 문서는 이 스크립트로 한 번 채운다.

무엇을 바꾸는가.
  - `documents.identity` 에 title·date 를 더한다(다른 값은 그대로)
  - 규정 문서의 `regulation_chunks.reg_title` 이 파일 이름이면 찾은 이름으로 바꾼다
이름을 못 찾았거나, 찾은 이름이 멀쩡한 파일 이름보다 못하면 건드리지 않는다.

사용:
  python scripts/89_backfill_titles.py            # 미리보기
  python scripts/89_backfill_titles.py --apply    # 백업 후 적용
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.doc_title import resolved_title  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all-chunks", action="store_true",
                    help="유형과 관계없이 규정 조각 이름도 바꾼다(코퍼스 DB 는 반입분이 전부 규정 조각)")
    args = ap.parse_args()
    db_path = Path(args.db)

    if args.apply:
        backup = db_path.parent / "backup"
        backup.mkdir(exist_ok=True)
        dest = backup / f"{db_path.stem}-titles-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(db_path, dest)
        print(f"백업 — {dest.name}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, filename, stored_path, masked_text, identity, doc_type FROM documents"
    ).fetchall()
    named = renamed_chunks = 0
    for r in rows:
        title, date = resolved_title({"filename": r["filename"], "stored_path": r["stored_path"],
                                      "masked_text": r["masked_text"], "identity": None})
        if not title:
            continue
        try:
            ident = json.loads(r["identity"] or "{}")
        except ValueError:
            ident = {}
        if ident.get("title") == title and ident.get("date") == date:
            continue
        ident["title"] = title
        if date:
            ident["date"] = date
        named += 1
        n = 0
        if r["doc_type"] == "regulation" or args.all_chunks:
            n = conn.execute(
                "SELECT COUNT(*) FROM regulation_chunks WHERE doc_id = ? AND reg_title = ?",
                (r["id"], r["filename"]),
            ).fetchone()[0]
        renamed_chunks += n
        print(f"  [{r['id']}] {r['filename'][:36]} → {title}{' · ' + date if date else ''}"
              f"{f' (조각 {n})' if n else ''}")
        if args.apply:
            conn.execute("UPDATE documents SET identity = ? WHERE id = ?",
                         (json.dumps(ident, ensure_ascii=False), r["id"]))
            if n:
                conn.execute(
                    "UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ? AND reg_title = ?",
                    (title, r["id"], r["filename"]))
    if args.apply:
        conn.commit()
    conn.close()
    print(f"\n{'적용' if args.apply else '미리보기'} — 문서 {named}건 · 규정 조각 {renamed_chunks}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
