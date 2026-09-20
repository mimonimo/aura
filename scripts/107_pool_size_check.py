#!/usr/bin/env python3
"""후보 개수를 늘리면 검색이 나아지는가 — 리랭커가 GPU 로 옮겨 싸졌으니 다시 재 본다.

리랭커는 후보 안의 순서만 바꾼다. 후보에 정답이 없으면 못 살린다. 지금은 어휘·임베딩
상위 12씩 융합해 10개만 리랭커에 넘긴다(CANDIDATE_LIMIT). VM CPU 시절에는 후보를 늘리면
질의당 시간이 비례해 늘어 못 늘렸는데(10개 3.75초), GPU 서비스는 후보 30개도 0.1초대다.

재는 것: 후보 풀에 정답이 들어 있는 비율(천장)과, 리랭킹 뒤 상위 10 지표.
사용 (VM 에서):  env PYTHONPATH=src ZZAIMY_RERANK_URL=… .venv/bin/python scripts/107_pool_size_check.py
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import regulations as reg  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.rerank import rerank_scored  # noqa: E402
from zzaimy.eval import retrieval_eval as rev  # noqa: E402

QUERY_SETS = (("정확한 질문", ""), ("상황 질문", "data/interim/synth_queries_paraphrase.jsonl"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--pools", default="10,20,30")
    args = ap.parse_args()
    pools = [int(x) for x in args.pools.split(",")]

    db = Database(Path(args.db))
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    for label, path in QUERY_SETS:
        rows = rev.load_rows(Path(path) if path else rev.QUERIES_PATH)
        golds, _ = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
        pairs = [(t, golds[i]) for i, r in enumerate(rows) if golds[i]
                 for qt in rev.QUERY_TYPES if (t := (r.get(qt) or "").strip())]
        idx = sorted(random.Random(rev.SEED).sample(range(len(pairs)), min(args.sample, len(pairs))))
        sample = [pairs[i] for i in idx]
        print(f"--- {label} · 질의 {len(sample)}건")
        for pool in pools:
            old_k = reg.HYBRID_TOP_K
            reg.HYBRID_TOP_K = max(pool, old_k)      # 융합에 넣는 순위 길이도 함께 늘린다
            runs, ceiling, t0 = [], 0, time.time()
            gold_list = []
            for q, g in sample:
                cands = reg.hybrid_candidates(db, q, chunks=chunks, limit=pool)
                ids = [c["id"] for c in cands]
                ceiling += any(c in g for c in ids)
                scored = rerank_scored(q, [by_id[c] for c in ids if c in by_id])
                runs.append([c["id"] for c, _ in scored] if scored else ids)
                gold_list.append(g)
            reg.HYBRID_TOP_K = old_k
            m = rev.metrics(runs, gold_list)
            print(f"   후보 {pool:2d}개 · 풀 안에 정답 {ceiling / len(sample):.3f}"
                  f" → R@1 {m['recall_at_1']:.3f} · R@5 {m['recall_at_5']:.3f}"
                  f" · R@10 {m['recall_at_10']:.3f} · MRR {m['mrr_at_10']:.3f}"
                  f" · 질의당 {(time.time() - t0) / len(sample):.2f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
