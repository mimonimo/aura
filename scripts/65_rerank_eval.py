"""리랭커 베이스라인 평가 — bge-reranker-v2-m3가 하이브리드 상위 10을 재정렬하면
얼마나 좋아지는지 실측한다 (ZZAIMY-Rerank 도입 판단 근거, 브리프 축 B).

절대 규칙 2: 파인튜닝 전에 베이스라인부터. 이 스크립트가 그 베이스라인이다.
실행:  PYTHONPATH=src:scripts OMP_NUM_THREADS=10 ZZAIMY_EMBED_DEVICE=cpu \
       .venv-train/bin/python scripts/65_rerank_eval.py
"""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

from zzaimy.app.db import Database
from zzaimy.app.embed_search import embed_search, rrf_merge
from zzaimy.eval.retrieval import mrr, recall_at_k

DB = Path("data/platform/platform.db")
QUERIES = Path("data/interim/synth_queries.jsonl")
REPORT = Path("docs/rerank-baseline.md")
TOP_K = 10
SAMPLE = 300  # 크로스인코더는 무겁다 — 표본 평가
MODEL = "BAAI/bge-reranker-v2-m3"


def main() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "s53", Path(__file__).parent / "53_retrieval_eval.py"
    )
    s53 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["s53"] = s53
    spec.loader.exec_module(s53)

    db = Database(DB)
    rows = [
        json.loads(x)
        for x in QUERIES.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    valid_ids = {c["id"] for c in db.list_regulation_chunks()}
    rows = [r for r in rows if r["chunk_id"] in valid_ids]
    pairs: list[tuple[str, int]] = []
    for r in rows:
        for qtype in ("practical", "requirement", "keyword"):
            q = (r.get(qtype) or "").strip()
            if q:
                pairs.append((q, r["chunk_id"]))
    random.seed(7)
    random.shuffle(pairs)
    pairs = pairs[:SAMPLE]
    print(f"표본 질의 {len(pairs)}건", flush=True)

    import sqlite3

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    texts = {
        row["id"]: f"{row['heading']} {row['content']}"[:800]
        for row in conn.execute(
            "SELECT id, heading, content FROM regulation_chunks"
        )
    }
    conn.close()

    from sentence_transformers import CrossEncoder

    ce = CrossEncoder(MODEL, device="cpu", max_length=512)

    base_runs: list[list[int]] = []
    rerank_runs: list[list[int]] = []
    golds: list[set[int]] = []
    for i, (q, gold) in enumerate(pairs):
        lex = s53.lexical_rank(db, q)
        den = [cid for cid, _ in embed_search(q, top_k=TOP_K)]
        cand = rrf_merge(lex, den)[:TOP_K]
        base_runs.append(list(cand))
        golds.append({gold})
        scored = ce.predict(
            [(q, texts.get(c, "")) for c in cand], show_progress_bar=False
        )
        order = [c for _, c in sorted(zip(scored, cand), reverse=True)]
        rerank_runs.append(order)
        if (i + 1) % 50 == 0:
            print(f"진행 {i + 1}/{len(pairs)}", flush=True)

    lines = [
        "# 리랭커 베이스라인 (bge-reranker-v2-m3, 학습 전)",
        "",
        f"측정일 {date.today().isoformat()} · 표본 질의 {len(pairs)}건 ·"
        " 후보 = 하이브리드(동가중) 상위 10",
        "",
        "| 구성 | Recall@1 | Recall@5 | MRR@10 |",
        "|---|---|---|---|",
        f"| 하이브리드만 | {recall_at_k(base_runs, golds, 1):.3f}"
        f" | {recall_at_k(base_runs, golds, 5):.3f} | {mrr(base_runs, golds):.3f} |",
        f"| + 리랭커 | {recall_at_k(rerank_runs, golds, 1):.3f}"
        f" | {recall_at_k(rerank_runs, golds, 5):.3f} | {mrr(rerank_runs, golds):.3f} |",
        "",
        "합성 질의 자가 검색 표본이므로 절대치는 낙관적 — 구성 간 비교용.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("RERANK_DONE")


if __name__ == "__main__":
    main()
