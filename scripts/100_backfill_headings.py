#!/usr/bin/env python3
"""표가 본문인 조각에 표제를 채운다 — 이미 적재된 조각에 새 규칙을 소급 적용한다.

왜 재처리가 아니라 백필인가: 표제는 조각 본문만 보고 정해지는 순수 함수
(`regulations._heading_of`)라, 파싱·OCR을 다시 돌릴 이유가 없다. 전체 재처리는
비전 판독·마스킹까지 다시 하므로 비싸고, 잘 들어온 결과를 흔들 위험이 있다.

표제는 임베딩 텍스트에 들어간다(`96_embed_on_thor.sh`: "제목 표제\\n본문").
따라서 적용 뒤에는 재색인이 필요하다 — 이 스크립트가 끝에 알려 준다.

사용:
  python3 scripts/100_backfill_headings.py                 # 미리보기(쓰지 않는다)
  python3 scripts/100_backfill_headings.py --apply         # 적용
  python3 scripts/100_backfill_headings.py --db 경로 --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.regulations import _heading_of  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 미리보기)")
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, doc_id, heading, content FROM regulation_chunks"
        " WHERE TRIM(COALESCE(heading, '')) = ''").fetchall()
    found = [(r["id"], r["doc_id"], _heading_of(r["content"] or "")) for r in rows]
    found = [(cid, did, h) for cid, did, h in found if h]
    by_doc = Counter(did for _, did, _ in found)
    print(f"표제 없는 조각 {len(rows)} · 새 표제 {len(found)} · 해당 문서 {len(by_doc)}")
    for cid, did, h in found[: args.show]:
        print(f"  조각 {cid} (문서 {did}) → {h}")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply 를 붙이십시오.")
        return 0
    with conn:
        conn.executemany("UPDATE regulation_chunks SET heading = ? WHERE id = ?",
                         [(h, cid) for cid, _, h in found])
    left = conn.execute("SELECT COUNT(*) FROM regulation_chunks"
                        " WHERE TRIM(COALESCE(heading, '')) = ''").fetchone()[0]
    print(f"적용 완료 {len(found)}건 · 남은 표제 없는 조각 {left}건")
    print("표제는 임베딩 텍스트에 들어갑니다 — 재색인하십시오: bash scripts/96_embed_on_thor.sh --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
