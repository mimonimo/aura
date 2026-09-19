#!/usr/bin/env python3
"""글자 손상 탐지 기준 비교 — 지금 기준과 제안 기준을 같은 자료에 대고 잰다.

지금 기준은 한 글자 한글 토큰의 비율을 본다. 그런데 한국어에는 "및·등·수·후·중"
처럼 한 글자로 쓰이는 낱말이 흔해서 멀쩡한 글도 걸린다.

제안 기준은 한 글자 토큰이 '연달아' 나오는지를 본다. 실제 OCR 분해는
"정 산 보 고 서"처럼 잇달아 끊기지, 흩어져 나오지 않는다. 사전이 필요 없다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import chunk_quality as cq   # noqa: E402
from zzaimy.app.db import Database           # noqa: E402


def longest_single_run(text: str) -> int:
    """한 글자 한글 토큰이 연달아 나온 최대 길이."""
    toks = [w for w in cq.normalize(text).split(" ") if cq._HANGUL.fullmatch(w)]
    best = run = 0
    for w in toks:
        run = run + 1 if len(w) == 1 else 0
        best = max(best, run)
    return best


def raw_runs(text: str) -> list[str]:
    """원문에서 한 글자 한글이 빈칸 하나로만 이어진 구간 — 구두점이 끼면 끊는다.

    구두점으로 갈라진 "가, 나 중"은 항목 기호이므로 손상이 아니다.
    "통 영 시 장"처럼 빈칸만으로 이어진 것이 진짜 분해다.
    """
    import re as _re

    out = []
    for seg in _re.split(r"[^가-힣 ]+", text or ""):
        toks = [w for w in seg.split(" ") if w]
        run: list[str] = []
        for w in toks:
            if len(w) == 1:
                run.append(w)
            else:
                if len(run) >= 2:
                    out.append("".join(run))
                run = []
        if len(run) >= 2:
            out.append("".join(run))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--run", type=int, default=3, help="제안 기준의 연속 길이")
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()

    db = Database(Path(args.db))
    texts: list[tuple[int, str]] = []
    for d in db.list_documents():
        for c in db.list_doc_chunks(d["id"]):
            t = (c.get("content") or "").strip()
            if t:
                texts.append((d["id"], t))
    for r in db.list_regulation_chunks():
        t = (r.get("content") or "").strip()
        if t:
            texts.append((r["doc_id"], t))

    now_hits, new_hits, both = [], [], 0
    for doc_id, t in texts:
        now = "글자 단위 분해" in cq.ocr_damage_signals(t)
        new = longest_single_run(t) >= args.run
        if now:
            now_hits.append((doc_id, t))
        if new:
            new_hits.append((doc_id, t))
        if now and new:
            both += 1

    n = len(texts)
    print(f"조각 {n}건")
    print(f"  지금 기준(한 글자 비율)  {len(now_hits)}건 ({len(now_hits)/max(n,1):.1%})")
    print(f"  제안 기준(연속 {args.run}자 이상) {len(new_hits)}건 ({len(new_hits)/max(n,1):.1%})")
    print(f"  두 기준이 함께 잡은 것 {both}건")

    from collections import Counter
    dist = Counter()
    samples: dict[int, list] = {}
    for doc_id, t in texts:
        for r2 in raw_runs(t):
            dist[len(r2)] += 1
            samples.setdefault(len(r2), []).append(r2)
    print("\n빈칸만으로 이어진 한 글자 구간의 길이 분포")
    for ln in sorted(dist):
        if ln > 9:
            continue
        ex = " · ".join(dict.fromkeys(samples[ln][:5]))
        print(f"  길이 {ln}: {dist[ln]:>5}건   보기: {ex[:70]}")
    long_total = sum(v for k, v in dist.items() if k >= 10)
    print(f"  길이 10 이상: {long_total}건")

    print("\n지금 기준만 잡은 글 (오탐 후보)")
    shown = 0
    for doc_id, t in now_hits:
        if longest_single_run(t) >= args.run:
            continue
        print(f"  [{doc_id}] {t[:110]}")
        shown += 1
        if shown >= args.samples:
            break
    print("\n제안 기준이 잡은 글")
    for doc_id, t in new_hits[:args.samples]:
        print(f"  [{doc_id}] 최대연속 {longest_single_run(t)} · {t[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
