#!/usr/bin/env python3
"""조각별 질문 축(doc2query)의 융합 가중을 정한다 — 홀드아웃 문서로만 잰다.

질문은 생성물이다. 검색에만 쓰고 근거로 내보이지 않지만, 그래도 이득이 있을 때만 켠다.
가중 0(꺼짐)부터 차례로 올려 보며 후보 진입률과 최종 지표를 함께 본다.

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/113_question_axis_check.py \
      --holdout 4,10,107 --weights 0,0.3,0.5,1.0
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import regulations as reg  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.embed_search import question_index_ready  # noqa: E402
from zzaimy.app.rerank import rerank_scored  # noqa: E402
from zzaimy.eval import retrieval_eval as rev  # noqa: E402

QUERY_SETS = (("정확한 질문", ""), ("상황 질문", "data/interim/synth_queries_paraphrase.jsonl"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", required=True, help="학습에 쓰지 않은 문서 id(쉼표)")
    ap.add_argument("--weights", default="0,0.3,0.5,1.0")
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    args = ap.parse_args()
    hold = {int(x) for x in args.holdout.split(",") if x.strip()}
    weights = [float(x) for x in args.weights.split(",")]

    if not question_index_ready():
        print("질문 색인이 없습니다 — scripts/111(생성)·112(임베딩) 을 먼저 돌리십시오.")
        return 1
    db = Database(Path(args.db))
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    prod = rev.production_retrievers(db, chunks)
    for name, path in QUERY_SETS:
        rows = rev.load_rows(Path(path) if path else rev.QUERIES_PATH)
        golds, _ = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
        pairs = [(t, golds[i]) for i, r in enumerate(rows) if golds[i]
                 and by_id[sorted(golds[i])[0]]["doc_id"] in hold
                 for qt in rev.QUERY_TYPES if (t := (r.get(qt) or "").strip())]
        if not pairs:
            print(f"--- {name}: 홀드아웃 질의 없음")
            continue
        print(f"--- {name} · 홀드아웃 질의 {len(pairs)}건")
        for w in weights:
            os.environ["ZZAIMY_QUESTION_W"] = str(w)
            runs, gl, entered = [], [], 0
            for q, g in pairs:
                cand = reg.hybrid_candidates(db, q, chunks=chunks,
                                             lexical_ids=prod.lexical(q), dense_ids=prod.dense(q),
                                             limit=reg.CANDIDATE_LIMIT)
                ids = [c["id"] for c in cand]
                entered += any(i in g for i in ids)
                sc = rerank_scored(q, [by_id[i] for i in ids if i in by_id])
                runs.append([c["id"] for c, _ in sc] if sc else ids)
                gl.append(g)
            m = rev.metrics(runs, gl)
            tag = "꺼짐" if w == 0 else f"가중 {w}"
            print(f"   질문 축 {tag:8s} · 진입률 {entered / len(pairs):.3f}"
                  f" → R@1 {m['recall_at_1']:.3f} · R@5 {m['recall_at_5']:.3f}"
                  f" · R@10 {m['recall_at_10']:.3f} · MRR {m['mrr_at_10']:.3f}", flush=True)
    os.environ.pop("ZZAIMY_QUESTION_W", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
