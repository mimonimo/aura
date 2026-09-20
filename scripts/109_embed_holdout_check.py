#!/usr/bin/env python3
"""①Embed 학습본 검증 — 학습에 쓰지 않은 문서의 질의로 '후보 진입률'과 최종 지표를 비교한다.

왜 이 지표인가: 리랭커는 후보 안의 순서만 바꾼다. 임베딩이 할 일은 **정답을 후보 20 안에
들여놓는 것**이다. v1 은 조밀 단독 지표만 보고 채택을 검토했다가 하이브리드에서 이득이
없어 접었다(2026-09-20). 그래서 여기서는 운영 경로 그대로 재고, 후보 진입률을 함께 적는다.

두 번 돈다: 지금 운영 임베딩(기본 환경) / 후보 임베딩(--cand-url·--cand-index).
조각 색인은 후보 모델로 미리 만들어 둬야 한다(`MODEL=… OUT_NAME=… scripts/96_embed_on_thor.sh`).

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/109_embed_holdout_check.py \
      --holdout 10,113,122 --cand-url http://토르:8016/embed \
      --cand-index /home/aura/zzaimy-capstone/data/platform/chunk_embeddings.v2.npz
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.eval import retrieval_eval as rev  # noqa: E402

QUERY_SETS = (("정확한 질문", ""), ("상황 질문", "data/interim/synth_queries_paraphrase.jsonl"))


def run(label: str, hold: set[int], db_path: str) -> None:
    """지금 환경변수가 가리키는 임베딩으로 홀드아웃 질의를 잰다."""
    # 임베딩 모듈은 불러올 때 색인 경로를 읽는다 — 환경변수를 바꾼 뒤 새로 불러온다
    import zzaimy.app.embed_search as es

    importlib.reload(es)
    import zzaimy.app.regulations as reg

    importlib.reload(reg)
    from zzaimy.app.db import Database
    from zzaimy.app.rerank import rerank_scored

    db = Database(Path(db_path))
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    prod = rev.production_retrievers(db, chunks)
    print(f"=== {label} (임베딩 활성 {prod.meta['embedding_active']})")
    for name, path in QUERY_SETS:
        rows = rev.load_rows(Path(path) if path else rev.QUERIES_PATH)
        golds, _ = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
        pairs = [(t, golds[i]) for i, r in enumerate(rows) if golds[i]
                 and by_id[sorted(golds[i])[0]]["doc_id"] in hold
                 for qt in rev.QUERY_TYPES if (t := (r.get(qt) or "").strip())]
        if not pairs:
            print(f"   {name}: 홀드아웃 질의 없음")
            continue
        runs, gold_list, entered = [], [], 0
        for q, g in pairs:
            cand = prod.hybrid(q, prod.lexical(q), prod.dense(q))
            entered += any(c in g for c in cand)
            scored = rerank_scored(q, [by_id[c] for c in cand if c in by_id])
            runs.append([c["id"] for c, _ in scored] if scored else list(cand))
            gold_list.append(g)
        m = rev.metrics(runs, gold_list)
        print(f"   {name} {len(pairs)}건 · 후보 진입률 {entered / len(pairs):.3f}"
              f" → R@1 {m['recall_at_1']:.3f} · R@5 {m['recall_at_5']:.3f}"
              f" · R@10 {m['recall_at_10']:.3f} · MRR {m['mrr_at_10']:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", required=True)
    ap.add_argument("--cand-url", default="")
    ap.add_argument("--cand-index", default="")
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    args = ap.parse_args()
    hold = {int(x) for x in args.holdout.split(",") if x.strip()}

    run("지금 운영 임베딩", hold, args.db)
    if args.cand_url and args.cand_index:
        os.environ["ZZAIMY_EMBED_URL"] = args.cand_url
        os.environ["ZZAIMY_EMBED_INDEX"] = args.cand_index
        run("후보 임베딩", hold, args.db)
    else:
        print("후보 임베딩 인자가 없어 운영 쪽만 쟀습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
