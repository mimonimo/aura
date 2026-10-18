#!/usr/bin/env python3
"""붙어 버린 어절 길이 분포 — 띄어쓰기 복원 기준을 실측으로 정한다."""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database   # noqa: E402

RUN = re.compile(r"[가-힣]{2,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    db = Database(Path(args.db))
    dist: Counter = Counter()
    samples: dict[int, list[str]] = {}
    for d in db.list_documents():
        for c in db.list_doc_chunks(d["id"]):
            for w in RUN.findall(c.get("content") or ""):
                dist[len(w)] += 1
                if len(w) >= 10:
                    samples.setdefault(len(w), []).append(w)

    total = sum(dist.values())
    print(f"한글 덩어리 {total}개")
    cum = 0
    for ln in sorted(dist):
        cum += dist[ln]
        if 6 <= ln <= 20:
            print(f"  길이 {ln:>2}: {dist[ln]:>6}개  (누적 {cum / total:.3%})")
    long = sum(v for k, v in dist.items() if k >= 21)
    print(f"  길이 21 이상: {long}개")
    print("\n길이별 보기")
    for ln in sorted(samples):
        if ln > 18:
            continue
        ex = " · ".join(dict.fromkeys(samples[ln])[:args.samples]) if False else \
             " · ".join(list(dict.fromkeys(samples[ln]))[:args.samples])
        print(f"  {ln:>2}: {ex[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
