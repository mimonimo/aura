#!/usr/bin/env python3
"""범위 밖(지출·증빙·정산 경로, 절대 규칙 11) 원본에서 들어온 라이브러리 문서를 뺀다 — 원본 파일은 그대로, 되돌릴 수 있게.

처리 대상 규칙(archive_state.exclusion_reason 「범위 밖 경로」)이 생기기 전에 들어온 문서가 라이브러리·검색에 남아 있다.
- 대상: 학교 원본 파일과 이어진 문서 가운데, 이어진 원본이 모두 범위 밖 경로인 것(기준 문서는 빼지 않는다).
- 지우기 전에 문서 행과 그 문서에 딸린 행(doc_id 칸이 있는 표 전부 — 조각·그림·개체·검토·어휘 색인 등)과
  원본·파일 장부·대화 출처·판본 이음을 한 파일(data/platform/backups/outscope-<시각>.jsonl.gz)에 남긴다.
- 지우기는 db.delete_document 와 같은 길(파생물까지), 원본 장부의 이음(doc_id)은 비운다 — 원본은 「처리 제외」로 보인다.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/187_remove_outscope_docs.py            # 미리 보기
          ... --apply
          ... --restore data/platform/backups/outscope-<시각>.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database, table_names  # noqa: E402

DOC_TABLES = ("doc_chunks", "doc_assets", "doc_entities", "mask_events", "reviews", "quality_reports", "draft_history",
              "grant_lex")
# 벡터 표(grant_vec)는 백업하지 않는다 — 되돌린 뒤 색인 따라잡기(grant_search, ADR-0057)가 빠진 조각을 다시 임베딩한다
LINKS = (("archive_files", "rel", "doc_id"), ("files", "id", "doc_id"), ("chat_sources", "id", "doc_id"),
         ("documents", "id", "version_of"), ("documents", "id", "related_criteria_id"))


def targets(db) -> list[int]:
    from zzaimy.app import archive_state as st
    orig, oa = st.original_sql()
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT rel, ext, size, dup_of, doc_id FROM archive_files WHERE removed_at = '' AND doc_id IS NOT NULL AND {orig}", oa)]
        reg = {int(r[0]) for r in conn.execute("SELECT id FROM documents WHERE doc_type = 'regulation'")}
    by_doc: dict[int, list[str]] = {}
    for r in rows:
        by_doc.setdefault(int(r["doc_id"]), []).append(st.exclusion_reason(r) or "처리 대상")
    return sorted(d for d, whys in by_doc.items() if d not in reg and all(w == "범위 밖 경로" for w in whys))


def _cols(conn, table: str) -> list[str]:
    cur = conn.execute(f"SELECT * FROM {table} LIMIT 0")
    return [c[0] for c in cur.description or []]


def backup(db, ids: list[int], out: Path) -> int:
    n = 0
    with db._conn() as conn, gzip.open(out, "wt", encoding="utf-8") as f:
        have = set(table_names(conn))
        for t in ("documents", *DOC_TABLES):
            if t not in have:
                continue
            cols = _cols(conn, t)
            key = "id" if t == "documents" else "doc_id"
            if key not in cols:
                continue
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for r in conn.execute(f"SELECT * FROM {t} WHERE {key} IN ({','.join('?' * len(part))})", part):
                    f.write(json.dumps({"t": t, "row": dict(zip(cols, r))}, ensure_ascii=False, default=str) + "\n")
                    n += 1
        for t, key, col in LINKS:                   # 지우면 비워지는 이음 — 되돌릴 때 다시 잇는다
            if t not in have:
                continue
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for k, v in conn.execute(f"SELECT {key}, {col} FROM {t} WHERE {col} IN ({','.join('?' * len(part))})", part):
                    f.write(json.dumps({"t": "link", "row": {"table": t, "key": key, "col": col, "k": k, "v": v}},
                                       ensure_ascii=False) + "\n")
    return n


def remove(db, ids: list[int]) -> None:
    with db._conn() as conn:
        have = set(table_names(conn))
    for i, did in enumerate(ids, 1):
        with db._conn() as conn:
            for t in ("grant_lex", "grant_vec"):
                if t in have:
                    conn.execute(f"DELETE FROM {t} WHERE doc_id = ?", (did,))
            conn.execute("UPDATE archive_files SET doc_id = NULL WHERE doc_id = ?", (did,))
        db.delete_document(did)
        if i % 500 == 0:
            print(f"  {i}/{len(ids)}", flush=True)


def restore(db, path: Path) -> None:
    rows = [json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8")]
    order = ["documents", *DOC_TABLES]
    with db._conn() as conn:
        have = set(table_names(conn))
        for t in (t for t in order if t in have):
            for rec in (r for r in rows if r["t"] == t):
                row = rec["row"]
                cols = list(row)
                conn.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT DO NOTHING",
                             [row[c] for c in cols])
        ok = {(t, k, c) for t, k, c in LINKS}
        for rec in (r["row"] for r in rows if r["t"] == "link"):
            if (rec["table"], rec["key"], rec["col"]) in ok:
                conn.execute(f"UPDATE {rec['table']} SET {rec['col']} = ? WHERE {rec['key']} = ?", (rec["v"], rec["k"]))
    print(f"되돌림 — {Counter(r['t'] for r in rows)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", default="")
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    if args.restore:
        restore(db, Path(args.restore))
        return 0
    ids = targets(db)
    with db._conn() as conn:
        sample = [r[0] for r in conn.execute(f"SELECT filename FROM documents WHERE id IN ({','.join('?' * min(8, len(ids)))})", ids[:8])] if ids else []
    print(f"범위 밖 원본에서만 온 라이브러리 문서 {len(ids)}건 — 예: {sample}")
    if not args.apply:
        print("미리 보기입니다 — --apply 로 백업 뒤 뺍니다")
        return 0
    out = ROOT / "data/platform/backups" / f"outscope-{time.strftime('%Y%m%d-%H%M%S')}.jsonl.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = backup(db, ids, out)
    print(f"백업 {n}줄 → {out}")
    remove(db, ids)
    print(f"뺐다 — {len(ids)}건. 되돌리기: scripts/187_remove_outscope_docs.py --restore {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
