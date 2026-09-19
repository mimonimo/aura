#!/usr/bin/env python3
"""글자 분해 복원 효과 측정 — 실제 적재 자료에 대고 얼마나 고쳐지는지 센다."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database          # noqa: E402
from zzaimy.app import text_repair as tr    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--samples", type=int, default=10)
    args = ap.parse_args()

    db = Database(Path(args.db))
    texts, labels = [], []
    for d in db.list_documents():
        for c in db.list_doc_chunks(d["id"]):
            t = (c.get("content") or "").strip()
            if t:
                texts.append(t); labels.append(d["id"])
    for r in db.list_regulation_chunks():
        t = (r.get("content") or "").strip()
        if t:
            texts.append(t); labels.append(r["doc_id"])

    fixed, stats = tr.repair_all(texts)
    print(f"조각 {stats['chunks']}건 · 고친 조각 {stats['changed']}건"
          f" ({stats['changed'] / max(stats['chunks'], 1):.1%})"
          f" · 붙인 낱말 {stats['joined']}개")
    print("\n자주 붙인 낱말")
    for word, n in stats["words"]:
        print(f"  {n:>3}  {word}")

    print(f"\n고쳐진 자리 (최대 {args.samples}건)")
    shown = 0
    for doc_id, before, after in zip(labels, texts, fixed):
        if before == after:
            continue
        # 달라진 첫 자리 주변만 보여 준다
        i = next((k for k in range(min(len(before), len(after)))
                  if before[k] != after[k]), 0)
        print(f"  [{doc_id}] 전: …{before[max(0, i - 25):i + 45]}…")
        print(f"        후: …{after[max(0, i - 25):i + 40]}…")
        shown += 1
        if shown >= args.samples:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
