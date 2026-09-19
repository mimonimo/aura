#!/usr/bin/env python3
"""반입 자가 점검 — 문서를 하나하나 열지 않고 반입 상태를 판정한다.

문서가 많으면 사람이 전수 검수할 수 없다. 기계가 적재된 자료에서 직접 세어
정상과 손볼 것을 가르고, 손볼 것만 심각한 순으로 보여 준다.

사용: python scripts/84_ingest_audit.py [--limit 20]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database          # noqa: E402
from zzaimy.app.ingest_audit import audit    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--samples", type=int, default=0,
                    help="손상으로 잡힌 조각의 실제 글을 이만큼 보여 준다")
    ap.add_argument("--doc", type=int, default=0, help="특정 문서만 본다")
    args = ap.parse_args()

    r = audit(Database(Path(args.db)))
    print(f"문서 {r['total']}건 · 정상 {r['healthy']}건 · 손볼 것 {r['flagged']}건"
          f" · 정상률 {r['rate']:.1%}")
    print(f"문서 정체 확정 {r['with_identity']}건 ({r['identity_rate']:.1%})")
    print(f"원본 파일 없음 {r['missing_original']}건 · 글자만 있는 문서 {r['text_only']}건")
    print("\n형식별")
    for name, n in r["by_suffix"]:
        print(f"  {n:>4}  {name}")
    print("\n갈래별")
    for name, n in r["by_type"]:
        print(f"  {n:>4}  {name}")
    if r["counts"]:
        print("\n유형별 건수")
        for name, n in r["counts"]:
            print(f"  {n:>4}  {name}")
    if r["rows"]:
        print(f"\n손볼 문서 (심각한 순, 최대 {args.limit}건)")
        for row in r["rows"][:args.limit]:
            print(f"  [{row['id']}] {row['filename'][:46]}")
            print(f"        조각 {row['n_chunks']} · 실질중앙 {row['median_substantive']}자"
                  f" · 잡음 {row['noise_ratio']:.0%} · 손상 {row['damage_ratio']:.0%}")
            for issue in row["issues"]:
                print(f"        - {issue}")
    if args.samples:
        from zzaimy.app import chunk_quality as cq
        from zzaimy.app.ingest_audit import _doc_blocks

        db = Database(Path(args.db))
        targets = ([args.doc] if args.doc
                   else [row["id"] for row in r["rows"] if row["damage_ratio"] > 0])
        print("\n손상으로 잡힌 조각의 실제 글")
        shown = 0
        for doc_id in targets:
            blocks, _ = _doc_blocks(db, doc_id)
            for b in blocks:
                text = (b["content"] or "").strip()
                signals = cq.ocr_damage_signals(text)
                if not signals:
                    continue
                print(f"  [{doc_id}] 신호 {' · '.join(signals)}")
                print(f"        {text[:180]}")
                shown += 1
                if shown >= args.samples:
                    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
