#!/usr/bin/env python3
"""'문서 추출'로 들어온 문서를 기준 문서로 올린다 — 다시 파싱하지 않고.

왜: 재반입에서 교내 내려받기 문서를 '문서 추출'로 넣어 검색 대상(기준 조각)에서 빠졌다.
원래 코퍼스는 기준 문서로 넣었다(scripts/74_ingest_corpus.py). 본문은 이미 저장돼 있으니
파싱을 반복하지 않고 반입과 같은 청킹·잡음 걸러내기를 그대로 써서 기준 조각만 만든다.

사용 (VM):
  env PYTHONPATH=src .venv/bin/python scripts/123_promote_to_criteria.py            # 미리보기
  env PYTHONPATH=src .venv/bin/python scripts/123_promote_to_criteria.py --apply
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.regulations import chunk_document, index_ready  # noqa: E402

# 공고(recruit)는 날실로 따로 처리한다 — 절대규칙 6(계열별 처리 경로 분리).
# 여기서 올리는 것은 갈래를 못 정해 '문서 추출'로 들어온 교내 서류뿐이다.
PROMOTE = ("auto", "")
MIN_CHARS = 80                 # 공개 코퍼스 반입(scripts/74 --min-chars)과 같은 하한


def targets(db: Database) -> list[dict]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, doc_type, sector, masked_text FROM documents "
            "WHERE COALESCE(doc_type, '') IN (?, ?) AND masked_text IS NOT NULL "
            "ORDER BY id", PROMOTE
        ).fetchall()
    return [dict(r) for r in rows if len((r["masked_text"] or "").strip()) >= MIN_CHARS]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Database(Path(args.db))
    todo = targets(db)
    before = len(db.list_regulation_chunks())
    print(f"기준 문서로 올릴 문서 {len(todo)}건 · 지금 기준 조각 {before}개")
    for d in todo[:5]:
        print(f"  {d['id']:>4} [{d['doc_type']}] {(d['filename'] or '')[:46]}")
    if not args.apply:
        print("미리보기입니다. 올리려면 --apply 를 붙이십시오.")
        return 0

    t0 = time.time()
    made = skipped = 0
    for i, d in enumerate(todo, 1):
        chunks = chunk_document(d["masked_text"])
        if not chunks:
            skipped += 1
            continue
        use, _dropped = index_ready(d["id"], chunks)     # 반입 경로와 같은 잡음 관문
        db.add_regulation_chunks(d["id"], d["filename"], use,
                                 sector=d.get("sector") or "common",
                                 dept=d.get("dept"), access_level="public")   # 기준 문서로 올리는 것 = 공개
        db.set_document_type(d["id"], "regulation")
        made += len(use)
        if i % 20 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} · 조각 {made}개 · {time.time() - t0:.0f}초", flush=True)

    (Path(args.db).parent / ".reindex-needed").touch()
    print(f"끝 — 기준 조각 {before} → {len(db.list_regulation_chunks())}개 (건너뜀 {skipped}건)")
    print("다음: MODEL=/models/zzaimy-embed-v2 bash scripts/96_embed_on_thor.sh --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
