#!/usr/bin/env python3
"""이미 들어와 있는 문서의 이름을 본문에서 찾아 채운다.

새로 들어오는 문서는 본문이 저장될 때 자동으로 채워진다(`Database.update_document`).
이 스크립트는 그 규칙이 생기기 전에 들어온 문서를 같은 방법으로 맞추는 용도다.

사용:
  python3 scripts/114_fill_doc_titles.py            # 미리보기
  python3 scripts/114_fill_doc_titles.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_title import display_name, find_title, head_text  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args()

    db = Database(Path(args.db))
    docs = db.list_documents()
    todo = []
    for d in docs:
        ident = db.get_doc_identity(d["id"])
        if ident.get("title"):
            continue
        title, date = find_title(head_text(d.get("stored_path"),
                                           (d.get("masked_text") or "")[:3000]))
        if title:
            todo.append((d, title, date))
    print(f"문서 {len(docs)}건 · 이름이 비어 있고 본문에서 찾은 것 {len(todo)}건")
    for d, title, date in todo[: args.show]:
        print(f"  {d['id']:>4} {d['filename'][:28]:<28} → {title}{' · ' + date if date else ''}")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply 를 붙이십시오.")
        return 0
    for d, _t, _dt in todo:
        db.fill_identity_from_text(d["id"])
    left = sum(1 for d in db.list_documents() if not db.get_doc_identity(d["id"]).get("title"))
    print(f"적용 완료 {len(todo)}건 · 아직 이름을 못 찾은 문서 {left}건")
    if left:
        print("  (본문에서 제목 줄을 찾지 못한 문서다 — 화면에는 파일 이름으로 남는다)")
    for d in db.list_documents()[:5]:
        print("  보기:", d["filename"], "→", display_name(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
