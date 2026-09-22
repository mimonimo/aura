#!/usr/bin/env python3
"""표 평문 다시 만들기 — 표 셀 JSON 은 그대로 두고, 그 안의 검색·인용용 평문(text)과 본문(masked_text)의
같은 자리만 새 규칙(render._row_texts: 가로 병합은 한 번만, 되풀이 행은 생략)으로 바꾼다.

원본을 다시 읽지 않는다(그림 판독·OCR 을 다시 돌리지 않아 몇 초면 끝난다). 본문에서 옛 평문을 찾지 못한
표는 건수로만 알린다. 끝나면 기준 조각 재분할(75)과 재색인(96)을 이어서.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/137_retable_text.py [--db data/platform/platform.db] [--apply]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app.render import render_table_text  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, doc_id, content FROM doc_chunks WHERE kind = 'table'").fetchall()
    changed: list[tuple[int, int, str, str, str]] = []     # chunk id, doc id, old text, new text, new content
    for r in rows:
        try:
            data = json.loads(r["content"])
            data["cells"]; int(data["n_rows"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        old = data.get("text") if isinstance(data.get("text"), str) else ""
        new = render_table_text(data)
        if new != old:
            data["text"] = new
            changed.append((r["id"], r["doc_id"], old, new, json.dumps(data, ensure_ascii=False)))
    docs = {d for _, d, *_ in changed}
    before = sum(len(o) for _, _, o, _, _ in changed)
    after = sum(len(n) for _, _, _, n, _ in changed)
    print(f"표 조각 {len(rows)}개 · 평문 바뀌는 것 {len(changed)}개(문서 {len(docs)}건) · 글자 {before:,} → {after:,}")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply")
        return 0
    backup = Path(args.db).with_name(f"platform-{datetime.now():%Y%m%d-%H%M%S}-before137.db")
    shutil.copy2(args.db, backup)
    print("백업:", backup)
    missed = 0
    for did in docs:
        text = conn.execute("SELECT masked_text FROM documents WHERE id = ?", (did,)).fetchone()[0] or ""
        for _, d, old, new, _ in changed:
            if d != did or not old:
                continue
            if old in text:
                text = text.replace(old, new, 1)
            else:
                missed += 1
        conn.execute("UPDATE documents SET masked_text = ? WHERE id = ?", (text, did))
    for cid, _, _, _, content in changed:
        conn.execute("UPDATE doc_chunks SET content = ? WHERE id = ?", (content, cid))
    conn.commit()
    print(f"적용: 표 조각 {len(changed)}개 · 본문 {len(docs)}건 · 본문에서 못 찾은 표 {missed}개")
    print("이어서: scripts/75_rechunk_regulations.py → scripts/96_embed_on_thor.sh --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
