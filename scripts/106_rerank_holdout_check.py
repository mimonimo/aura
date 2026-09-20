#!/usr/bin/env python3
"""리랭커 학습본 검증 — 학습에 쓰지 않은 문서의 질의만으로 운영 경로에서 비교한다.

왜 따로 만드는가: 평가 표본(150건)에는 학습에 쓴 문서의 질의가 섞여 있어 그대로 재면
학습본에 유리하게 나온다. 여기서는 홀드아웃 문서의 질의만 골라, 같은 하이브리드 후보에
베이스와 학습본을 각각 매겨 비교한다(같은 후보 = 짝지은 비교).

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/106_rerank_holdout_check.py \
      --holdout 10,113,122 --base http://토르:8013/score --trained http://토르:8015/score
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.eval import retrieval_eval as rev  # noqa: E402

QUERY_SETS = (("정확한 질문", ""), ("상황 질문", "data/interim/synth_queries_paraphrase.jsonl"))


def score(url: str, query: str, texts: list[str], max_length: int = 512) -> list[float]:
    body = json.dumps({"query": query, "texts": texts, "max_length": max_length}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = json.loads(r.read().decode())["scores"]
    return [1.0 / (1.0 + math.exp(-float(x))) for x in raw]      # 운영과 같은 0~1 눈금


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", required=True, help="학습에 쓰지 않은 문서 id(쉼표)")
    ap.add_argument("--base", required=True)
    ap.add_argument("--trained", required=True)
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    args = ap.parse_args()
    hold = {int(x) for x in args.holdout.split(",") if x.strip()}

    db = Database(Path(args.db))
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    prod = rev.production_retrievers(db, chunks)
    for label, path in QUERY_SETS:
        rows = rev.load_rows(Path(path) if path else rev.QUERIES_PATH)
        golds, _ = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
        pairs = [(t, golds[i]) for i, r in enumerate(rows) if golds[i]
                 and by_id[sorted(golds[i])[0]]["doc_id"] in hold
                 for qt in rev.QUERY_TYPES if (t := (r.get(qt) or "").strip())]
        if not pairs:
            print(f"--- {label}: 홀드아웃 질의 없음")
            continue
        base_runs, trained_runs, hyb, gold_list = [], [], [], []
        for q, g in pairs:
            cand = prod.hybrid(q, prod.lexical(q), prod.dense(q))[: rev.TOP_K]
            ids = [c for c in cand if c in by_id]
            texts = [f"{by_id[c]['reg_title']} {by_id[c]['heading']}\n{(by_id[c]['content'] or '')[:900]}"
                     for c in ids]
            hyb.append(ids); gold_list.append(g)
            for url, out in ((args.base, base_runs), (args.trained, trained_runs)):
                s = score(url, q, texts)
                out.append([ids[i] for i in sorted(range(len(ids)), key=lambda i: (-s[i], i))])
        print(f"--- {label} · 홀드아웃 질의 {len(pairs)}건")
        for name, runs in (("하이브리드", hyb), ("베이스", base_runs), ("학습본", trained_runs)):
            m = rev.metrics(runs, gold_list)
            print(f"   {name:8s} R@1 {m['recall_at_1']:.3f} · R@5 {m['recall_at_5']:.3f}"
                  f" · R@10 {m['recall_at_10']:.3f} · MRR {m['mrr_at_10']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
