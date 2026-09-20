#!/usr/bin/env python3
"""LLM 리랭커 평가 — 같은 질의 세트·같은 표본으로 하이브리드·크로스인코더·LLM 리랭커를 비교한다.

운영 검색 구성요소(production_retrievers)를 그대로 쓰고 리랭크 자리만 바꿔 끼운다.
결과는 data/eval/llm-rerank-<날짜>.json 과 docs/llm-rerank-eval.md 에 남긴다.
후보 모델·색인을 환경변수로 바꿔 돌린 임시 실행분은 docs/ 에 쓰지 않고 data/eval/ 에만 둔다.

사용 (VM):
  PYTHONPATH=src .venv/bin/python scripts/90_llm_rerank_eval.py \
      --llm "토르02 qwen3:4b=http://211.170.162.120:11434/v1|qwen3:4b" \
      --llm "토르03 qwen3:30b-a3b=http://211.170.162.121:11434/v1|qwen3:30b-a3b" --sample 150
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.llm_rerank import make_reranker  # noqa: E402
from zzaimy.eval import retrieval_eval as rev  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="append", default=[], help="이름=주소|모델")
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--no-cross", action="store_true", help="크로스인코더 비교 생략")
    ap.add_argument("--queries", default="", help="질의 세트 경로(기본: 합성 세트)")
    ap.add_argument("--tag", default="", help="산출물 이름 꼬리표(예: paraphrase)")
    ap.add_argument("--expand", action="append", default=[], help="질의 확장 비교: 이름=주소|모델")
    ap.add_argument("--conditional", action="store_true", help="조건부 확장(1위 근거가 약할 때만) 행 추가")
    ap.add_argument("--db", default="", help="평가할 DB(기본: 운영 DB) — 운영 DB 가 바뀌는 중일 때 스냅숏으로")
    args = ap.parse_args()

    db = Database(Path(args.db) if args.db else ROOT / "data" / "platform" / "platform.db")
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    rows = rev.load_rows(Path(args.queries) if args.queries else rev.QUERIES_PATH)
    # 질의·정답 묶기는 정본 평가(run_eval)와 같은 방식 — 정답 본문으로 현재 조각에 재결선
    row_golds, _stats = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
    pairs = [(text, row_golds[i]) for i, r in enumerate(rows) if row_golds[i]
             for qtype in rev.QUERY_TYPES if (text := (r.get(qtype) or "").strip())]
    queries = [t for t, _ in pairs]
    golds = [g for _, g in pairs]
    idx = sorted(random.Random(rev.SEED).sample(range(len(queries)), min(args.sample, len(queries))))
    queries = [queries[i] for i in idx]
    golds = [golds[i] for i in idx]

    prod = rev.production_retrievers(db, chunks)
    print(f"질의 {len(queries)}건 · 조각 {len(chunks)} · 임베딩 {prod.meta['embedding_active']}", flush=True)
    hyb = []
    t0 = time.time()
    for q in queries:
        lex, den = prod.lexical(q), prod.dense(q)
        hyb.append(prod.hybrid(q, lex, den)[: rev.TOP_K])
    results = [{"method": "하이브리드(리랭크 없음)", **rev.metrics(hyb, golds),
                "sec_per_query": round((time.time() - t0) / len(queries), 2)}]
    print(results[-1], flush=True)

    # 질의 확장 — 확장한 질의로 후보를 새로 뽑아(하이브리드) 같은 지표로 잰다. 크로스인코더도 확장 질의로.
    from zzaimy.app.query_expand import expand
    for spec in args.expand:
        name, rest = spec.split("=", 1)
        url, model = rest.split("|", 1)
        t0 = time.time()
        eq = [expand(q, url, model) for q in queries]
        t_exp = (time.time() - t0) / len(queries)
        ehyb = [prod.hybrid(q, prod.lexical(q), prod.dense(q))[: rev.TOP_K] for q in eq]
        results.append({"method": f"질의 확장 {name} + 하이브리드", **rev.metrics(ehyb, golds),
                        "sec_per_query": round(t_exp + 0.5, 2)})
        print(results[-1], flush=True)
        print("  예:", eq[0][:120], flush=True)
        # 조건부 확장 — 원 질의 1위의 크로스인코더 점수가 하한 미만(근거 약함)일 때만 확장 순위를 쓴다
        if args.conditional:
            from zzaimy.app.rerank import RERANK_MIN, rerank_scored

            by_id = {c["id"]: c for c in chunks}
            t0 = time.time()
            cond, n_exp = [], 0
            for q, base, ex in zip(queries, hyb, ehyb):
                sc = rerank_scored(q, [by_id[i] for i in base if i in by_id]) or []
                weak = not sc or sc[0][1] < RERANK_MIN
                cond.append(ex if weak else base)
                n_exp += weak
            results.append({"method": f"조건부 확장 {name} (약할 때만, {n_exp}/{len(queries)}건 확장)",
                            **rev.metrics(cond, golds),
                            "sec_per_query": round((time.time() - t0) / len(queries) + t_exp, 2)})
            print(results[-1], flush=True)
        # 원 질의·확장 질의 순위를 RRF 로 합친다 — 원 질의에 더 큰 가중(정확한 질문의 정밀도 보존)
        for w_exp in (0.5, 0.3):
            fused = []
            for base, ex in zip(hyb, ehyb):
                sc: dict = {}
                for rank, cid in enumerate(base):
                    sc[cid] = sc.get(cid, 0.0) + 1.0 / (60 + rank)
                for rank, cid in enumerate(ex):
                    sc[cid] = sc.get(cid, 0.0) + w_exp / (60 + rank)
                fused.append([cid for cid, _ in sorted(sc.items(), key=lambda kv: -kv[1])][: rev.TOP_K])
            results.append({"method": f"원 질의 + 확장 {name} 융합(가중 {w_exp})", **rev.metrics(fused, golds),
                            "sec_per_query": round(t_exp + 1.0, 2)})
            print(results[-1], flush=True)
        if not args.no_cross and prod.rerank is not None:
            t0 = time.time()
            runs = [list(prod.rerank(oq, cand))[: rev.TOP_K] for oq, cand in zip(queries, ehyb)]
            results.append({"method": f"질의 확장 {name} + 하이브리드 + 크로스인코더", **rev.metrics(runs, golds),
                            "sec_per_query": round(t_exp + (time.time() - t0) / len(queries), 2)})
            print(results[-1], flush=True)

    rankers = []
    if not args.no_cross and prod.rerank is not None:
        rankers.append(("크로스인코더 bge-reranker-v2-m3", prod.rerank))
    for spec in args.llm:
        name, rest = spec.split("=", 1)
        url, model = rest.split("|", 1)
        rr = make_reranker(url, model)
        rankers.append((f"LLM {name}", lambda q, ids, rr=rr: [c["id"] for c in rr(q, [by_id[i] for i in ids if i in by_id])]))

    for name, fn in rankers:
        t0 = time.time()
        runs = [list(fn(q, cand))[: rev.TOP_K] for q, cand in zip(queries, hyb)]
        results.append({"method": name, **rev.metrics(runs, golds),
                        "sec_per_query": round((time.time() - t0) / len(queries), 2)})
        print(results[-1], flush=True)

    tag = f"-{args.tag}" if args.tag else ""
    out = ROOT / "data" / "eval" / f"llm-rerank{tag}-{date.today()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"n": len(queries), "seed": rev.SEED, "rows": results},
                              ensure_ascii=False, indent=2))
    md = [f"# LLM 리랭커 평가{(' — ' + args.tag) if args.tag else ''}", "",
          f"측정 {date.today()} · 질의 {len(queries)}건(고정 시드 {rev.SEED} 표본) · 규정 조각 {len(chunks)}개. "
          "모든 행은 같은 하이브리드 후보 상위 10을 재정렬한다.", "",
          "| 방식 | R@1 | R@5 | R@10 | MRR@10 | 질의당 초 |", "|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['method']} | {r['recall_at_1']:.3f} | {r['recall_at_5']:.3f} | "
                  f"{r['recall_at_10']:.3f} | {r['mrr_at_10']:.3f} | {r['sec_per_query']} |")
    md += ["", f"원자료: `{out.relative_to(ROOT)}` (산출: `scripts/90_llm_rerank_eval.py`)", ""]
    # 저장소에 남기는 보고서는 기본 실행분만. 후보 모델·색인을 바꿔 돌린 임시 실행분은
    # data/eval/ 에만 둔다 — 그래야 VM 에서 돌려도 배포(깃 상태 확인)를 막지 않는다.
    trial = bool(os.environ.get("ZZAIMY_EMBED_MODEL") or os.environ.get("ZZAIMY_EMBED_INDEX"))
    md_out = (out.with_suffix(".md") if trial
              else ROOT / "docs" / f"llm-rerank-eval{tag}.md")
    md_out.write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
