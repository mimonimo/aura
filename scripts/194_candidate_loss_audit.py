#!/usr/bin/env python3
"""규정 검색 후보 단계 손실 감사 — 융합 순위에는 정답이 있는데 리랭커 입력 후보 20 에서 빠진 까닭.

왜: 학습 누수를 뺀 합성 질의(scripts/193 과 같은 선택)에서 임베딩 원순위 상위 20 정답 진입률은
0.955 인데 운영 후보(select_candidates — 품질 판정 SEARCH 강도 + 같은 본문 중복 제거) 20 은
0.906 이다.
리랭커는 후보 20 만 다시 줄 세우므로 이 차가 운영 상한을 깎는다. 질의마다 정답 조각이 어느 단계에서
빠졌는지 나눠 센다.

채점: 정답 집합은 retrieval_eval.ChunkMatcher 그대로
(같은 본문의 다른 조각 포함 — 이 감사로 고친 것).
옛 채점(원래 id 하나)으로 잰 값도 함께 남긴다(base_old_scoring 등).

분류(정답이 후보 20 에 없는 질의만, 융합 순위에서 가장 앞선 정답 조각 기준):
  fusion_miss    정답이 어휘 20 ∪ 임베딩 20 어디에도 없다(후보 단계 이전 손실)
  body_dup       같은 본문이 이미 후보에 있어 빠짐 — 대신 남은 조각이 후보 20 안이면 채점 문제
  reason:<이름>  품질 판정 사유(chunk_quality 의 reason)로 빠짐 — 여럿이면 이름을 + 로 잇는다
  past_20        정답이 걸러지지 않았는데 앞쪽 조각들에 밀려 20 밖
                 (융합 원순위 20 안이었는지 따로 셈)
대신 남은 조각(substitute): 빠진 정답 조각의 본문을 거의 다 담은(조각 12-gram 의 90% 이상) 후보가
20 안에 있으면 내용은 후보에 들어간 것으로 본다.

변형(--variants): chunk_quality 의 사유를 SEARCH 강도에서 하나씩 풀어(표시만) 후보 지표를 다시 잰다.
리랭커 표본은 기준과 변형의 후보 집합이 다른 질의만 다시 부른다.

사용 (VM, 측정 클론 저장소 루트에서 — .env.local 을 읽고 띄운다. 순위 캐시는 193 이 만든 것):
  PYTHONPATH=src .venv/bin/python scripts/194_candidate_loss_audit.py \\
      --train-export embed2-data.json --holdout 4,10,... --cache rank-cache.jsonl --out audit.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.eval import retrieval_eval as rev  # noqa: E402


def _load_193():
    spec = importlib.util.spec_from_file_location(
        "sweep193", ROOT / "scripts" / "193_hybrid_weight_clean_sweep.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sw = _load_193()


def trace_select(
    merged_ids: list[int], by_id: dict[int, dict], limit: int
) -> tuple[list[int], dict]:
    """select_candidates 를 그대로 따라가며 조각마다 운명을 적는다.

    {id: ('kept'|'body_dup'|'drop'(사유들)|'unseen', 위치)}
    """
    import re

    from zzaimy.app.chunk_quality import Strictness, assess, corpus_reasons

    ordered = [by_id[cid] for cid in merged_ids if cid in by_id]
    fate: dict[int, tuple] = {}
    if not ordered:
        return [], fate
    extra = corpus_reasons(ordered)
    seen_body: set[str] = set()
    kept: list[int] = []
    stop = len(ordered)
    for i, c in enumerate(ordered):
        body = re.sub(r"\s+", " ", c.get("content") or "").strip()
        if body in seen_body:
            fate[c["id"]] = ("body_dup", i)
            continue
        v = assess(
            c.get("content") or "",
            {"extra_reasons": [r for r in extra.get(i, []) if r != "boilerplate"]},
        )
        if not v.keep(Strictness.SEARCH):
            from zzaimy.app.chunk_quality import _DROP_AT

            why = [r for r in v.reasons if _DROP_AT.get(r, Strictness.INDEX) <= Strictness.SEARCH]
            fate[c["id"]] = ("drop", i, tuple(why))
            continue
        seen_body.add(body)
        kept.append(c["id"])
        fate[c["id"]] = ("kept", i)
        if len(kept) >= limit:
            stop = i + 1
            break
    for c in ordered[stop:]:
        fate.setdefault(c["id"], ("unseen", None))
    return kept, fate


def covers(container: str, part: str, cache: dict) -> bool:
    """container 가 part 본문을 거의 다 담는가(part 12-gram 의 90% 이상)."""
    from zzaimy.app.chunk_quality import _shingles

    key = id(part)
    ps = cache.get(key)
    if ps is None:
        ps = cache[key] = _shingles(part)
    if not ps:
        return False
    cs = _shingles(container)
    return len(ps & cs) / len(ps) >= 0.9


def classify(q: dict, merged: list[int], kept: list[int], fate: dict, by_id: dict) -> dict:
    gold = set(q["gold"])
    if any(c in gold for c in kept):
        return {"cls": "hit"}
    in_merged = [c for c in merged if c in gold]
    if not in_merged:
        return {"cls": "fusion_miss"}
    raw20 = any(c in gold for c in merged[:20])
    # 걸러진 정답 조각(앞선 것부터) — 하나라도 걸러졌으면 그 사유로
    for g in in_merged:
        f = fate.get(g, ("unseen", None))
        if f[0] in ("body_dup", "drop"):
            gtxt = by_id[g].get("content") or ""
            cache: dict = {}
            sub = next(
                (k for k in kept if covers(by_id[k].get("content") or "", gtxt, cache)), None
            )
            name = "body_dup" if f[0] == "body_dup" else "reason:" + "+".join(f[2])
            return {
                "cls": name,
                "gold_id": g,
                "merged_pos": merged.index(g),
                "raw20": raw20,
                "substitute": sub,
            }
    return {"cls": "past_20", "raw20": raw20, "merged_pos": merged.index(in_merged[0])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-export", required=True)
    ap.add_argument("--holdout", required=True)
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--queries", default=str(rev.QUERIES_PATH))
    ap.add_argument("--cache", required=True, help="193 의 순위 캐시(jsonl)")
    ap.add_argument("--variants", default="", help="SEARCH 에서 풀어 볼 사유(쉼표, + 로 묶음)")
    ap.add_argument("--rerank-sample", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--examples", type=int, default=12, help="사유별로 남길 본문 표본 수")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import zzaimy.app.chunk_quality as cq
    import zzaimy.app.regulations as reg
    from zzaimy.app import embed_search as es
    from zzaimy.app.db import Database

    t0 = time.time()
    hold = {int(x) for x in args.holdout.split(",") if x.strip()}
    tr = sw.load_training(Path(args.train_export), hold)
    db = Database(Path(args.db))
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    matcher = rev.ChunkMatcher(chunks)

    # 193 과 같은 깨끗한 합성 질의
    rows = rev.load_rows(Path(args.queries))
    rows, _ = sw.split_rows(rows, tr)
    golds, _ = rev.resolve_golds(rows, matcher, None)
    queries: list[dict] = []
    for r, g in zip(rows, golds):
        if r["_leak"] or not g:
            continue
        # 옛 채점(10-15 까지) — id·본문이 그대로면 그 id 하나만 정답, 같은 본문의 다른 조각은 오답
        gid = rev._gold_id(r)
        same = gid in matcher.by_id and matcher._norm[gid] == rev.normalize_text(r.get("gold_text"))
        g_old = {gid} if same else g
        for qt in rev.QUERY_TYPES:
            q = (r.get(qt) or "").strip()
            if q and rev.normalize_text(q) not in tr["train_queries"]:
                queries.append({"q": q, "qtype": qt, "gold": sorted(g), "gold_old": sorted(g_old)})
    cache = {}
    for line in Path(args.cache).read_text(encoding="utf-8").splitlines():
        x = json.loads(line)
        cache[x["q"]] = x
    missing = [q for q in queries if q["q"] not in cache]
    print(f"질의 {len(queries)} · 캐시 없음 {len(missing)}", flush=True)
    for q in missing:
        cache[q["q"]] = {
            "lex": reg.lexical_rank(db, q["q"], chunks=chunks)[: reg.HYBRID_TOP_K],
            "den": [cid for cid, _ in es.embed_search(q["q"], top_k=reg.HYBRID_TOP_K)],
        }
    allowed = set(by_id)
    w_a = reg.HYBRID_W_A

    def merged_of(q: dict) -> list[int]:
        x = cache[q["q"]]
        den = [c for c in x["den"] if c in allowed]
        return es.rrf_merge(list(x["lex"])[: reg.HYBRID_TOP_K], den, w_a=w_a, w_b=1.0)

    report: dict = {"n": len(queries), "w_a": w_a, "limit": reg.CANDIDATE_LIMIT}
    cls: Counter = Counter()
    sub_cnt: Counter = Counter()
    raw20_cnt: Counter = Counter()
    examples: dict[str, list] = {}
    base_kept: list[list[int]] = []
    mism = 0
    for i, q in enumerate(queries):
        merged = merged_of(q)
        kept, fate = trace_select(merged, by_id, reg.CANDIDATE_LIMIT)
        prod = [c["id"] for c in reg.select_candidates(merged, by_id, reg.CANDIDATE_LIMIT)]
        mism += prod != kept
        base_kept.append(prod)
        c = classify(q, merged, kept, fate, by_id)
        q["_cls"] = c
        cls[c["cls"]] += 1
        if c["cls"] != "hit":
            raw20_cnt[c["cls"]] += bool(c.get("raw20"))
        if c.get("substitute") is not None:
            sub_cnt[c["cls"]] += 1
        if c["cls"] not in ("hit", "fusion_miss", "past_20"):
            ex = examples.setdefault(c["cls"], [])
            if len(ex) < args.examples:
                g = by_id[c["gold_id"]]
                ex.append(
                    {
                        "q": q["q"],
                        "gold_id": c["gold_id"],
                        "merged_pos": c["merged_pos"],
                        "gold_title": g.get("title") or g.get("source") or "",
                        "gold_text": (g.get("content") or "")[:600],
                        "substitute": c.get("substitute"),
                        "substitute_text": (by_id[c["substitute"]].get("content") or "")[:300]
                        if c.get("substitute") is not None
                        else None,
                    }
                )
        if (i + 1) % 2000 == 0:
            print(f"감사 {i + 1}/{len(queries)} ({time.time() - t0:.0f}초)", flush=True)
    n = len(queries)
    report["trace_mismatch"] = mism
    report["classes"] = {k: {"n": v, "share": round(v / n, 4)} for k, v in cls.most_common()}
    report["raw20_among_loss"] = dict(raw20_cnt)
    report["substitute_in20"] = dict(sub_cnt)
    report["examples"] = examples
    report["dense_raw_in20"] = round(
        sum(1 for q in queries if set(q["gold"]) & set(cache[q["q"]]["den"][:20])) / n, 4
    )
    report["dense_raw_in20_old_scoring"] = round(
        sum(1 for q in queries if set(q["gold_old"]) & set(cache[q["q"]]["den"][:20])) / n, 4
    )
    report["fused_raw_in20"] = round(
        sum(1 for q in queries if set(q["gold"]) & set(merged_of(q)[:20])) / n, 4
    )
    pq_base = [sw.per_query(k, set(q["gold"])) for k, q in zip(base_kept, queries)]
    report["base"] = sw.summarize(pq_base)
    report["base_old_scoring"] = sw.summarize(
        [sw.per_query(k, set(q["gold_old"])) for k, q in zip(base_kept, queries)]
    )
    report["gold_size"] = {
        "same_text_rows": sum(1 for q in queries if len(q["gold"]) > len(q["gold_old"])),
        "mean_new": round(sum(len(q["gold"]) for q in queries) / n, 3),
        "mean_old": round(sum(len(q["gold_old"]) for q in queries) / n, 3),
    }
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "classes",
                    "substitute_in20",
                    "raw20_among_loss",
                    "dense_raw_in20",
                    "dense_raw_in20_old_scoring",
                    "fused_raw_in20",
                    "base",
                    "base_old_scoring",
                    "gold_size",
                )
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    # 변형 — 사유를 SEARCH 에서 풀어 다시 잰다(운영 함수 그대로, 판정 표만 바꾼다)
    variants = [v for v in args.variants.split(",") if v.strip()]
    report["variants"] = {}
    from zzaimy.app.chunk_quality import Strictness

    sidx = sorted(random.Random(args.seed).sample(range(n), min(args.rerank_sample, n)))
    prod_rr = None
    if args.rerank_sample:
        prod_rr = rev.production_retrievers(db, chunks).rerank
    rcache: dict[tuple, list[int]] = {}

    def rerank(i: int, cands: list[int]) -> list[int]:
        key = (i, tuple(sorted(cands)))
        if key not in rcache:
            rcache[key] = list(prod_rr(queries[i]["q"], cands))
        return rcache[key]

    rr_base = None
    if prod_rr is not None:
        rr_base = [sw.per_query(rerank(i, base_kept[i]), set(queries[i]["gold"])) for i in sidx]
        report["rerank_base"] = sw.summarize(rr_base)
        report["rerank_base_old_scoring"] = sw.summarize(
            [sw.per_query(rerank(i, base_kept[i]), set(queries[i]["gold_old"])) for i in sidx]
        )
        print("리랭크 옛 채점", json.dumps(report["rerank_base_old_scoring"]), flush=True)
        print("리랭크 기준", json.dumps(report["rerank_base"]), flush=True)
    orig = dict(cq._DROP_AT)
    for v in variants:
        for r in v.split("+"):
            cq._DROP_AT[r] = Strictness.INDEX
        try:
            kept_v = [
                [c["id"] for c in reg.select_candidates(merged_of(q), by_id, reg.CANDIDATE_LIMIT)]
                for q in queries
            ]
        finally:
            cq._DROP_AT.clear()
            cq._DROP_AT.update(orig)
        pq_v = [sw.per_query(k, set(q["gold"])) for k, q in zip(kept_v, queries)]
        out = {
            "hybrid": sw.summarize(pq_v),
            "changed_queries": sum(1 for a, b in zip(base_kept, kept_v) if a != b),
            "delta": {k: sw.paired_delta(pq_base, pq_v, k) for k in ("in20", "r1", "r5", "mrr")},
        }
        if rr_base is not None:
            rr_v = [sw.per_query(rerank(i, kept_v[i]), set(queries[i]["gold"])) for i in sidx]
            out["rerank"] = sw.summarize(rr_v)
            out["rerank_delta"] = {
                k: sw.paired_delta(rr_base, rr_v, k) for k in ("in20", "r1", "r5", "mrr")
            }
        report["variants"][v] = out
        print(
            "변형",
            v,
            json.dumps(out, ensure_ascii=False),
            f"({time.time() - t0:.0f}초)",
            flush=True,
        )
    report["elapsed_s"] = round(time.time() - t0, 1)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("끝", args.out, report["elapsed_s"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
