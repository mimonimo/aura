#!/usr/bin/env python3
"""하이브리드 어휘 가중(HYBRID_W_A) 재측정 — 임베딩 학습에 쓰지 않은 문서의 질의만으로.

왜: 운영 임베딩(ZZAIMY-Embed v2)은 평가와 같은 생성기로 만든 합성 질의로 학습했다. 평가 질의의 정답
문서가 학습 문서면 임베딩 단독 지표가 부풀고, 그 수치로 고른 어휘 가중은 임베딩 쪽으로 기운다.
그래서 학습 내보내기(scripts/108 의 data.json — 질의·정답 조각·조각 본문)를 읽어 학습 문서를
본문(gold_text)·제목(gold_title) 기준으로 가려내고, 남은 질의로 가중을 다시 잰다.

누수 판정(행 단위, 하나라도 걸리면 제외):
  - 제목: 정답 규정 제목이 학습 문서(홀드아웃이 아닌 문서) 조각의 제목과 같다
  - 본문: 정답 본문이 학습 문서 조각 본문과 겹친다(평가의 정답 재결선과 같은 겹침 기준)
  - 질의: 질의 문자열이 학습 질의와 같다(질의 단위)

지표: 후보 진입률(정답이 리랭커 입력 후보 20 안), 하이브리드 R@1·R@5·MRR@10, 일부 가중은
리랭커 뒤 지표(고정 시드 표본). 구간은 비율은 윌슨 95%, 가중 간 차이는 짝지은 부트스트랩 95%.

사용 (VM, 저장소 루트에서 — .env.local 을 읽고 띄운다):
  set -a; . ./.env.local; set +a
  PYTHONPATH=src .venv/bin/python scripts/191_hybrid_weight_clean_sweep.py \\
      --train-export /path/to/embed2-data.json --holdout 4,10,107,... --out /tmp/sweep.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.eval import retrieval_eval as rev  # noqa: E402

WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.6, 1.0)
PARAPHRASE_PATH = Path("data/interim/synth_queries_paraphrase.jsonl")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 4), round(c + h, 4))


def per_query(run: list[int], gold: set[int]) -> dict:
    """질의 하나의 지표 — 순위 run 은 후보 순서(최대 20)."""
    hit = next((k for k, c in enumerate(run) if c in gold), None)
    return {
        "in20": hit is not None and hit < 20,
        "r1": hit is not None and hit < 1,
        "r5": hit is not None and hit < 5,
        "r10": hit is not None and hit < 10,
        "rr": 1.0 / (hit + 1) if hit is not None and hit < 10 else 0.0,
    }


def summarize(pq: list[dict]) -> dict:
    n = len(pq)
    out = {"n": n}
    for key in ("in20", "r1", "r5", "r10"):
        k = sum(1 for x in pq if x[key])
        out[key] = round(k / n, 4) if n else None
        out[key + "_ci"] = wilson(k, n)
    out["mrr"] = round(sum(x["rr"] for x in pq) / n, 4) if n else None
    return out


def paired_delta(a: list[dict], b: list[dict], key: str, reps: int = 2000, seed: int = 1) -> dict:
    """b − a 의 평균 차와 짝지은 부트스트랩 95% 구간(같은 질의끼리)."""
    val = (lambda x: x["rr"]) if key == "mrr" else (lambda x: 1.0 if x[key] else 0.0)
    d = [val(y) - val(x) for x, y in zip(a, b)]
    n = len(d)
    if not n:
        return {"delta": None}
    rng = random.Random(seed)
    means = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return {
        "delta": round(sum(d) / n, 4),
        "ci": (round(means[int(0.025 * reps)], 4), round(means[int(0.975 * reps) - 1], 4)),
        "better": sum(1 for x in d if x > 0),
        "worse": sum(1 for x in d if x < 0),
    }


# ---------------------------------------------------------------- 누수 가르기


def load_training(path: Path, holdout: set[int]) -> dict:
    """108 내보내기에서 학습 문서 조각(제목 줄·본문)과 학습 질의를 꺼낸다 — 108 의 선택 그대로."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    train_q = [
        r for r in d["queries"] if r["set"] == "synth" and r["doc_id"] not in holdout and r["negs"]
    ]
    train_chunks = [c for c in d["chunks"] if c["doc_id"] not in holdout]
    hold_chunks = [c for c in d["chunks"] if c["doc_id"] in holdout]
    first = lambda c: rev.normalize_text(c["text"].split("\n", 1)[0])  # noqa: E731
    body = lambda c: c["text"].split("\n", 1)[1] if "\n" in c["text"] else ""  # noqa: E731
    return {
        "n_docs": len({c["doc_id"] for c in d["chunks"]}),
        "n_train_docs": len({c["doc_id"] for c in train_chunks}),
        "train_queries": {rev.normalize_text(r["q"]) for r in train_q},
        "n_train_queries": len(train_q),
        "train_heads": [first(c) for c in train_chunks],
        "hold_heads": [first(c) for c in hold_chunks],
        "train_matcher": rev.ChunkMatcher(
            {"id": c["id"], "content": body(c)} for c in train_chunks
        ),
    }


def title_in(title: str, heads: list[str], cache: dict) -> bool:
    """조각 첫 줄은 「제목 표제」 꼴 — 제목이 첫 줄의 앞머리면 그 문서로 본다.

    앞머리가 같은 다른 제목도 걸리는 보수적 판정이다.
    """
    t = rev.normalize_text(title)
    if not t:
        return False
    if t not in cache:
        cache[t] = any(h == t or h.startswith(t + " ") for h in heads)
    return cache[t]


def split_rows(rows: list[dict], tr: dict) -> tuple[list[dict], Counter]:
    """행마다 누수 여부와 출처(옛 홀드아웃·학습 뒤 새 문서)를 붙인다."""
    tc, hc = {}, {}
    stats: Counter = Counter()
    for r in rows:
        by_title = title_in(r.get("gold_title") or "", tr["train_heads"], tc)
        by_text = (
            bool(tr["train_matcher"].resolve(None, r.get("gold_text") or ""))
            if r.get("gold_text")
            else False
        )
        r["_leak"] = by_title or by_text
        r["_origin"] = (
            "hold" if title_in(r.get("gold_title") or "", tr["hold_heads"], hc) else "new"
        )
        stats["rows"] += 1
        stats["leak_title"] += by_title
        stats["leak_text"] += by_text
        stats["leak_text_only"] += by_text and not by_title
        stats["leak"] += r["_leak"]
        if not r["_leak"]:
            stats["clean_" + r["_origin"]] += 1
    return rows, stats


# ---------------------------------------------------------------- 측정


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-export", required=True, help="scripts/108 이 토르로 보낸 data.json")
    ap.add_argument("--holdout", required=True, help="108 report.json 의 holdout_docs (쉼표)")
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--queries", default=str(rev.QUERIES_PATH))
    ap.add_argument("--limit", type=int, default=0, help="깨끗한 합성 질의 표본 수(0=전부)")
    ap.add_argument("--rerank-sample", type=int, default=500)
    ap.add_argument("--rerank-weights", default="0.0,0.2,0.3,0.4")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--cache", default="", help="질의별 어휘·임베딩 순위 캐시(jsonl) — 다시 돌릴 때 재사용"
    )
    ap.add_argument(
        "--weights", default=",".join(map(str, WEIGHTS)), help="융합할 가중(0.4 는 늘 포함)"
    )
    ap.add_argument(
        "--leaked", action="store_true", help="비교용 — 누수로 가른 쪽(학습 문서의 질의)을 잰다"
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import zzaimy.app.regulations as reg
    from zzaimy.app import embed_search as es
    from zzaimy.app.db import Database

    t0 = time.time()
    weights = sorted({float(x) for x in args.weights.split(",") if x.strip()} | {0.4})
    hold = {int(x) for x in args.holdout.split(",") if x.strip()}
    tr = load_training(Path(args.train_export), hold)
    print(
        f"학습 내보내기: 문서 {tr['n_docs']}(학습 {tr['n_train_docs']})"
        f" · 학습 질의 {tr['n_train_queries']}",
        flush=True,
    )

    db = Database(Path(args.db))
    chunks = db.list_regulation_chunks()
    matcher = rev.ChunkMatcher(chunks)
    prod = rev.production_retrievers(db, chunks)
    print("메타", json.dumps(prod.meta, ensure_ascii=False), flush=True)
    if not prod.meta["embedding_active"]:
        print("임베딩 비활성 — .env.local 을 읽고 띄우십시오", flush=True)
        return 2

    report: dict = {
        "meta": prod.meta,
        "train": {k: tr[k] for k in ("n_docs", "n_train_docs", "n_train_queries")},
        "holdout_docs": sorted(hold),
        "weights": weights,
    }
    queries: list[dict] = []  # {q, qtype, set, origin, gold}
    for set_name, path, qtypes in (
        ("synth", Path(args.queries), rev.QUERY_TYPES),
        ("paraphrase", PARAPHRASE_PATH, ("practical",)),
    ):
        rows = rev.load_rows(path)
        rows, stats = split_rows(rows, tr)
        golds, gstats = rev.resolve_golds(rows, matcher, None)
        q_leak = 0
        for r, g in zip(rows, golds):
            if r["_leak"] != args.leaked or not g:
                continue
            for qt in qtypes:
                q = (r.get(qt) or "").strip()
                if not q:
                    continue
                if not args.leaked and rev.normalize_text(q) in tr["train_queries"]:
                    q_leak += 1
                    continue
                queries.append(
                    {
                        "q": q,
                        "qtype": qt,
                        "set": set_name,
                        "origin": r["_origin"],
                        "gold": sorted(g),
                    }
                )
        stats["query_leak"] = q_leak
        stats["unmapped"] = gstats.get("unmapped", 0)
        report.setdefault("split", {})[set_name] = dict(stats)
        print(set_name, json.dumps(dict(stats), ensure_ascii=False), flush=True)

    synth = [q for q in queries if q["set"] == "synth"]
    para = [q for q in queries if q["set"] == "paraphrase"]
    if args.limit and len(synth) > args.limit:
        synth = random.Random(args.seed).sample(synth, args.limit)
    queries = synth + para
    report["n_queries"] = {
        "synth": len(synth),
        "paraphrase": len(para),
        "synth_by_origin": dict(Counter(q["origin"] for q in synth)),
        "synth_by_qtype": dict(Counter(q["qtype"] for q in synth)),
    }
    print("측정 질의", json.dumps(report["n_queries"], ensure_ascii=False), flush=True)

    # 질의당 어휘·임베딩 순위 한 번 — 가중만 바꿔 다시 융합
    cache: dict[str, dict] = {}
    cpath = Path(args.cache) if args.cache else None
    if cpath and cpath.exists():
        for line in cpath.read_text(encoding="utf-8").splitlines():
            x = json.loads(line)
            cache[x["q"]] = x
    cf = cpath.open("a", encoding="utf-8") if cpath else None
    for i, q in enumerate(queries, 1):
        if q["q"] not in cache:
            x = {
                "q": q["q"],
                "lex": prod.lexical(q["q"])[: reg.HYBRID_TOP_K],
                "den": [cid for cid, _ in es.embed_search(q["q"], top_k=reg.HYBRID_TOP_K)],
            }
            cache[q["q"]] = x
            if cf:
                cf.write(json.dumps(x, ensure_ascii=False) + "\n")
        q["lex"], q["den"] = cache[q["q"]]["lex"], cache[q["q"]]["den"]
        if i % 500 == 0:
            print(f"순위 {i}/{len(queries)} ({time.time() - t0:.0f}초)", flush=True)
    if cf:
        cf.close()

    def candidates(q: dict, w: float) -> list[int]:
        reg.HYBRID_W_A = w  # 운영 함수 그대로 — 가중 상수만 바꾼다
        return [
            c["id"]
            for c in reg.hybrid_candidates(
                db, q["q"], chunks=chunks, lexical_ids=list(q["lex"]), dense_ids=list(q["den"])
            )
        ]

    orig_w = reg.HYBRID_W_A
    pq: dict[float, list[dict]] = {}
    cands: dict[float, list[list[int]]] = {}
    for w in weights:
        cands[w] = [candidates(q, w) for q in queries]
        pq[w] = [per_query(c, set(q["gold"])) for c, q in zip(cands[w], queries)]
        print(f"w={w} 융합 끝 ({time.time() - t0:.0f}초)", flush=True)
    reg.HYBRID_W_A = orig_w

    def groups(items: list[int]) -> dict[str, list[int]]:
        g = {
            "합성 전체": [i for i in items if queries[i]["set"] == "synth"],
            "상황 질문(바꿔 쓴)": [i for i in items if queries[i]["set"] == "paraphrase"],
        }
        for qt in rev.QUERY_TYPES:
            g[f"합성·{qt}"] = [
                i for i in items if queries[i]["set"] == "synth" and queries[i]["qtype"] == qt
            ]
        for o in ("hold", "new"):
            g[f"합성·{o}"] = [
                i for i in items if queries[i]["set"] == "synth" and queries[i]["origin"] == o
            ]
        return g

    allidx = list(range(len(queries)))
    report["hybrid"] = {}
    for gname, idx in groups(allidx).items():
        report["hybrid"][gname] = {str(w): summarize([pq[w][i] for i in idx]) for w in weights}
        report["hybrid"][gname]["delta_vs_0.4"] = {
            str(w): {
                k: paired_delta([pq[0.4][i] for i in idx], [pq[w][i] for i in idx], k)
                for k in ("in20", "r5", "mrr")
            }
            for w in weights
            if w != 0.4
        }
    # 임베딩 단독 원순위(후보 거르기 전) — 참고
    report["dense_raw"] = summarize(
        [per_query(q["den"], set(q["gold"])) for q in queries if q["set"] == "synth"]
    )
    report["lexical_raw"] = summarize(
        [per_query(q["lex"], set(q["gold"])) for q in queries if q["set"] == "synth"]
    )

    # 리랭커 뒤 — 표본(합성 고정 시드 + 상황 질문 전부), 같은 후보 목록은 한 번만 부른다
    rweights = [float(x) for x in args.rerank_weights.split(",") if x.strip()]
    if prod.rerank is not None and args.rerank_sample:
        sidx = [i for i in allidx if queries[i]["set"] == "synth"]
        sidx = sorted(random.Random(args.seed).sample(sidx, min(args.rerank_sample, len(sidx))))
        sidx += [i for i in allidx if queries[i]["set"] == "paraphrase"]
        rcache: dict[tuple, list[int]] = {}
        rr: dict[float, dict[int, dict]] = {w: {} for w in rweights}
        calls = 0
        for j, i in enumerate(sidx, 1):
            for w in rweights:
                key = (i, tuple(sorted(cands[w][i])))  # 리랭커 점수는 후보 집합에만 달렸다
                if key not in rcache:
                    rcache[key] = list(prod.rerank(queries[i]["q"], cands[w][i]))
                    calls += 1
                rr[w][i] = per_query(rcache[key], set(queries[i]["gold"]))
            if j % 50 == 0:
                print(
                    f"리랭크 {j}/{len(sidx)} · 호출 {calls} ({time.time() - t0:.0f}초)", flush=True
                )
        report["rerank"] = {"calls": calls, "sample_seed": args.seed}
        for gname, idx in groups(sidx).items():
            if not idx:
                continue
            report["rerank"][gname] = {str(w): summarize([rr[w][i] for i in idx]) for w in rweights}
            report["rerank"][gname]["hybrid_same_sample"] = {
                str(w): summarize([pq[w][i] for i in idx]) for w in rweights
            }
            if 0.4 in rweights:
                report["rerank"][gname]["delta_vs_0.4"] = {
                    str(w): {
                        k: paired_delta([rr[0.4][i] for i in idx], [rr[w][i] for i in idx], k)
                        for k in ("in20", "r1", "r5", "mrr")
                    }
                    for w in rweights
                    if w != 0.4
                }
    report["elapsed_s"] = round(time.time() - t0, 1)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("끝", args.out, report["elapsed_s"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
