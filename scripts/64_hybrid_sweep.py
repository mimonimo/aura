"""하이브리드 가중 스윕 — RRF의 어휘:임베딩 가중을 격자 실측으로 고른다.

53과 같은 질의 세트·지표 정본을 쓰되, 어휘/임베딩 순위를 질의당 한 번만
계산해 두고 가중 조합만 바꿔 재평가한다 (저비용).
실행:  PYTHONPATH=src .venv-train/bin/python scripts/64_hybrid_sweep.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from zzaimy.app import regulations as reg  # noqa: F401  (경로 초기화 부수효과)
from zzaimy.app.db import Database
from zzaimy.app.embed_search import embed_search, rrf_merge
from zzaimy.eval.retrieval import mrr, recall_at_k

DB = Path("data/platform/platform.db")
QUERIES = Path("data/interim/synth_queries.jsonl")
REPORT = Path("docs/retrieval-weight-sweep.md")
TOP_K = 10
WEIGHTS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2]


def main() -> None:
    import importlib.util
    import sys

    # 53의 lexical_rank를 재사용한다 (구현 갈림 방지)
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
    print(f"질의 소스 {len(rows)}조각", flush=True)

    # 질의당 어휘·임베딩 순위를 1회 계산
    lex_runs: list[list[int]] = []
    den_runs: list[list[int]] = []
    golds: list[set[int]] = []
    n = 0
    for i, r in enumerate(rows):
        gold = r["chunk_id"]
        for qtype in ("practical", "requirement", "keyword"):
            q = (r.get(qtype) or "").strip()
            if not q:
                continue
            lex_runs.append(s53.lexical_rank(db, q))
            den_runs.append([cid for cid, _ in embed_search(q, top_k=TOP_K)])
            golds.append({gold})
            n += 1
        if (i + 1) % 100 == 0:
            print(f"순위 계산 {i + 1}/{len(rows)}", flush=True)

    lines = [
        "# 하이브리드 가중 스윕 (RRF 어휘 가중 w_a, 임베딩 w_b=1.0)",
        "",
        f"측정일 {date.today().isoformat()} · 질의 {n}건 · 지표 정본(eval/retrieval)",
        "",
        "| w_a | Recall@1 | Recall@5 | Recall@10 | MRR@10 |",
        "|---|---|---|---|---|",
    ]
    best = (None, -1.0)
    for w in WEIGHTS:
        merged = [
            rrf_merge(lex, den, w_a=w, w_b=1.0)[:TOP_K]
            for lex, den in zip(lex_runs, den_runs)
        ]
        m = mrr(merged, golds)
        lines.append(
            f"| {w} | {recall_at_k(merged, golds, 1):.3f}"
            f" | {recall_at_k(merged, golds, 5):.3f}"
            f" | {recall_at_k(merged, golds, 10):.3f} | {m:.3f} |"
        )
        if m > best[1]:
            best = (w, m)
        print(f"w_a={w} MRR={m:.3f}", flush=True)
    lines += ["", f"최적 w_a = {best[0]} (MRR {best[1]:.3f}) — 서비스 기본값 판단 근거."]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("SWEEP_DONE", best[0])


if __name__ == "__main__":
    main()
