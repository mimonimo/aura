#!/usr/bin/env python3
"""이미 적재된 문서를 지금 규칙으로 다시 가린다.

왜 필요한가. 마스킹 규칙을 고쳐도 이미 저장된 글은 예전 규칙으로 가려진 채 남는다.
전 문서를 다시 파싱하면 몇 시간이 걸리므로, 저장된 글만 골라 다시 가린다.

무엇을 건드리는가. `documents.masked_text`, `doc_chunks.content`,
`regulation_chunks.content` 세 곳이다. 원본 파일과 좌표·구조는 건드리지 않는다.
표 조각은 JSON 이므로 칸의 글만 가리고 구조는 그대로 둔다.

안전 장치. 기본은 미리보기이며 `--apply` 를 줘야 실제로 쓴다. 바꾸기 전에
`data/platform/backup/` 아래로 DB 를 복사한다.

사용:
  python scripts/88_remask_documents.py            # 무엇이 바뀌는지만 본다
  python scripts/88_remask_documents.py --apply    # 실제로 다시 가린다
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

from zzaimy.ingest.pii import PiiMasker, RawDocument   # noqa: E402


def mask_text(masker: PiiMasker, text: str, doc_id: int) -> tuple[str, int]:
    """한 덩이를 가린다. (가린 글, 가린 건수)."""
    if not (text or "").strip():
        return text, 0
    out = masker.mask(RawDocument(doc_id=doc_id, text=text))
    doc, events = out if isinstance(out, tuple) else (out, [])
    return getattr(doc, "text", text), len(events)


def mask_table_json(masker: PiiMasker, raw: str, doc_id: int) -> tuple[str, int]:
    """표 조각 — 칸의 글만 가리고 행·열 구조는 그대로 둔다."""
    try:
        table = json.loads(raw)
    except (ValueError, TypeError):
        return raw, 0
    if not isinstance(table, dict):
        return raw, 0
    hits = 0
    # 좌표 조각 — {"bbox": [...], "text": "..."} 꼴은 글 칸만 가린다
    if isinstance(table.get("text"), str):
        fixed, n = mask_text(masker, table["text"], doc_id)
        if n:
            table["text"] = fixed
            hits += n
    cells = table.get("cells")
    if not isinstance(cells, list):
        return (json.dumps(table, ensure_ascii=False), hits) if hits else (raw, 0)
    for cell in cells:
        # 셀은 [row, col, rowspan, colspan, flag, text] 꼴이다
        if isinstance(cell, list) and cell and isinstance(cell[-1], str):
            fixed, n = mask_text(masker, cell[-1], doc_id)
            if n:
                cell[-1] = fixed
                hits += n
    # 옆 칸 문맥으로만 알아볼 수 있는 성명('과 장 | 김진형') — 행 단위로 한 번 더
    from zzaimy.ingest.pii import mask_names_in_rows

    rowwise = mask_names_in_rows(cells)
    extra = sum(1 for a, b in zip(cells, rowwise) if a[-1] != b[-1])
    if extra:
        table["cells"] = rowwise
        hits += extra
        if isinstance(table.get("text"), str):
            from zzaimy.app.render import render_table_text

            table["text"] = render_table_text(table)
    if not hits:
        return raw, 0
    return json.dumps(table, ensure_ascii=False), hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--limit", type=int, default=0, help="문서 수 제한 (시험용)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"저장소를 찾지 못했습니다 — {db_path}", file=sys.stderr)
        return 2

    if args.apply:
        backup_dir = db_path.parent / "backup"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{db_path.stem}-remask-{stamp}.db"
        shutil.copy2(db_path, dest)
        print(f"되돌릴 수 있게 복사해 두었습니다 — {dest.name}")

    masker = PiiMasker()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 마스킹 정책을 따른다 — 기준 문서는 가리지 않는다(ADR-0006), 단 코퍼스 반입분은 가린다
    from zzaimy.app.pii_audit import is_masking_subject

    docs = [d for d in conn.execute("SELECT id, filename, doc_type, owner FROM documents ORDER BY id")
            if is_masking_subject(d["doc_type"], d["owner"])]
    if args.limit:
        docs = docs[:args.limit]

    total_hits = 0
    touched_docs = 0
    for d in docs:
        doc_id = d["id"]
        hits = 0

        row = conn.execute("SELECT masked_text FROM documents WHERE id = ?",
                           (doc_id,)).fetchone()
        if row and row["masked_text"]:
            fixed, n = mask_text(masker, row["masked_text"], doc_id)
            if n:
                hits += n
                if args.apply:
                    conn.execute("UPDATE documents SET masked_text = ? WHERE id = ?",
                                 (fixed, doc_id))

        for c in conn.execute(
            "SELECT id, kind, content FROM doc_chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall():
            body = c["content"] or ""
            if c["kind"] == "table" or body.lstrip().startswith("{"):
                fixed, n = mask_table_json(masker, body, doc_id)
            else:
                fixed, n = mask_text(masker, body, doc_id)
            if n:
                hits += n
                if args.apply:
                    conn.execute("UPDATE doc_chunks SET content = ? WHERE id = ?",
                                 (fixed, c["id"]))

        for c in conn.execute(
            "SELECT id, content FROM regulation_chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall():
            fixed, n = mask_text(masker, c["content"] or "", doc_id)
            if n:
                hits += n
                if args.apply:
                    conn.execute(
                        "UPDATE regulation_chunks SET content = ? WHERE id = ?",
                        (fixed, c["id"]))

        if hits:
            touched_docs += 1
            total_hits += hits
            print(f"  [{doc_id}] {d['filename'][:46]} — {hits}건")

    if args.apply:
        conn.commit()
    conn.close()

    head = "다시 가렸습니다" if args.apply else "미리보기입니다 (아직 저장하지 않았습니다)"
    print(f"\n{head} — 문서 {touched_docs}건 · 가린 자리 {total_hits}곳")
    if not args.apply and total_hits:
        print("실제로 적용하려면 --apply 를 붙여 다시 실행하십시오.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
