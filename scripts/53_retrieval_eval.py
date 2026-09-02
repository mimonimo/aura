"""검색 미니 베이스라인 측정 (규정 코퍼스 + 합성 질의).

합성 질의(51)의 정답 조각을 세 가지 검색 방식으로 얼마나 찾는지 잰다:
어휘(Kiwi), 임베딩(KURE), 하이브리드(RRF). Recall@1/5/10, MRR@10.

주의: 합성 질의는 같은 코퍼스에서 생성된 것이라 절대치는 낙관적이다.
방식 간 상대 비교와 회귀 감지용 미니 벤치마크로만 쓴다. (정식 베이스라인은
P3에서 파일럿 코퍼스 + 사람 골드셋으로 측정 — eval-plan.md)

실행(Spark): .venv/bin/python scripts/53_retrieval_eval.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from zzaimy.app import regulations as reg
from zzaimy.app.db import Database
from zzaimy.app.embed_search import embed_search, rrf_merge

QUERIES = Path("data/interim/synth_queries.jsonl")
REPORT = Path("docs/retrieval-baseline-mini.md")
TOP_K = 10


def lexical_rank(db: Database, query: str) -> list[int]:
    """Kiwi 어휘 랭킹만 (find_relevant의 어휘 부분 재현)."""
    import math

    q = reg.extract_nouns(query)
    if not q:
        return []
    chunks = db.list_regulation_chunks()
    for c in chunks:
        if c["id"] not in reg._noun_cache:
            reg._noun_cache[c["id"]] = reg.extract_nouns(c["content"])
    n = max(len(chunks), 1)
    df = {t: sum(1 for c in chunks if t in reg._noun_cache[c["id"]]) for t in q}
    idf = {t: math.log(1 + n / (1 + df[t])) for t in q}
    rare_cut = max(3, int(n * 0.1))
    scored = []
    for c in chunks:
        m = q & reg._noun_cache[c["id"]]
        if len(m) >= 2:
            rare = sum(1 for t in m if df[t] <= rare_cut)
            score = sum(min(len(t), 4) * idf[t] for t in m)
            scored.append((rare, score, len(m), c["id"]))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [cid for _, _, _, cid in scored[:TOP_K]]


def main() -> None:
    db = Database("data/platform/platform.db")
    rows = [json.loads(x) for x in QUERIES.read_text(encoding="utf-8").splitlines() if x.strip()]
    valid_ids = {c["id"] for c in db.list_regulation_chunks()}
    rows = [r for r in rows if r["chunk_id"] in valid_ids]
    print(f"평가 질의 소스 {len(rows)}조각", flush=True)

    # 지표는 정본 구현(zzaimy.eval.retrieval)으로 계산 — 논문 표와 동일 코드
    from zzaimy.eval.retrieval import mrr as _mrr
    from zzaimy.eval.retrieval import recall_at_k

    runs: dict[str, list[list]] = defaultdict(list)
    golds: dict[str, list[set]] = defaultdict(list)

    for i, r in enumerate(rows):
        gold = r["chunk_id"]
        for qtype in ("practical", "requirement", "keyword"):
            query = (r.get(qtype) or "").strip()
            if not query:
                continue
            lex = lexical_rank(db, query)
            den = [cid for cid, _ in embed_search(query, top_k=TOP_K)]
            hyb = rrf_merge(lex, den)[:TOP_K]
            hyw = rrf_merge(lex, den, w_a=0.4, w_b=1.0)[:TOP_K]
            for name, ranking in (
                ("어휘(Kiwi)", lex), ("임베딩(KURE)", den),
                ("하이브리드(동가중)", hyb), ("하이브리드(임베딩 우세)", hyw),
            ):
                runs[name].append(list(ranking))
                golds[name].append({gold})
        if (i + 1) % 100 == 0:
            print(f"진행 {i + 1}/{len(rows)}", flush=True)

    lines = [
        "# 검색 미니 베이스라인 (규정 코퍼스 · 합성 질의)",
        "",
        f"측정일 {date.today().isoformat()} · 조각 {len(valid_ids)}개 · "
        f"질의 {len(runs.get('하이브리드(동가중)', []))}건 · 임베딩 KURE-v1 · 어휘 Kiwi 명사+IDF",
        "",
        "합성 질의 자가 검색이므로 절대치는 낙관적 — 방식 간 비교·회귀 감지용.",
        "",
        "| 방식 | Recall@1 | Recall@5 | Recall@10 | MRR@10 |",
        "|---|---|---|---|---|",
    ]
    for name in ("어휘(Kiwi)", "임베딩(KURE)", "하이브리드(동가중)", "하이브리드(임베딩 우세)"):
        rl, gs = runs[name], golds[name]
        lines.append(
            f"| {name} | {recall_at_k(rl, gs, 1):.3f} | {recall_at_k(rl, gs, 5):.3f} |"
            f" {recall_at_k(rl, gs, 10):.3f} | {_mrr(rl, gs):.3f} |"
        )
    report = "\n".join(lines) + "\n"
    REPORT.write_text(report, encoding="utf-8")
    print(report, flush=True)


if __name__ == "__main__":
    main()
