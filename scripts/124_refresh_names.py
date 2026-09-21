#!/usr/bin/env python3
"""문서 이름을 지금의 규칙으로 다시 매긴다.

이름 규칙(doc_title.py·db._unique_name)을 고치면 이미 들어온 문서는 옛 이름을 그대로 쓴다.
올라온 파일 이름(identity.original_filename)으로 되돌린 뒤 반입과 같은 함수를 다시 부른다 —
규칙이 한 곳에만 있으므로 화면·인용·검색이 같은 이름을 본다.

사용 (VM):
  env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py               # 미리보기
  env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --apply
  env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --like ' | '   # 일부만
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--like", default="", help="이 글자가 이름에 든 문서만")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Database(Path(args.db))
    from zzaimy.app.pipeline import DocumentProcessor

    ask = DocumentProcessor().ask_review      # 규칙이 못 찾은 것만 모델에게 제목 줄을 짚게 한다
    sql = ("SELECT id, filename, identity, masked_text FROM documents "
           "WHERE masked_text IS NOT NULL")
    params: tuple = ()
    if args.like:
        sql += " AND filename LIKE ?"
        params = (f"%{args.like}%",)
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]

    changed = 0
    for r in rows:
        ident = json.loads(r["identity"] or "{}") or {}
        orig = (ident.get("original_filename") or "").strip()
        if not orig:
            continue
        if args.apply:
            with db._conn() as conn:
                conn.execute("UPDATE documents SET filename = ? WHERE id = ?", (orig, r["id"]))
            new = db.rename_from_text(r["id"], r["masked_text"], overwrite=True, ask=ask)
        else:
            from zzaimy.app.doc_title import display_name
            # 저장된 이름을 무시하고 본문에서 다시 읽는다 — --apply 와 같은 조건으로 미리 본다
            new = display_name({**r, "filename": orig, "identity": None})
        if new and new != r["filename"]:
            changed += 1
            if changed <= 10 or args.apply:
                print(f"  {r['id']:>4} {r['filename'][:52]}  →  {new[:52]}")
    print(f"{'바꿈' if args.apply else '바뀔 것'} {changed}건 / 대상 {len(rows)}건")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply 를 붙이십시오.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
