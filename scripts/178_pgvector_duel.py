"""의미 검색 벡터 저장 대결 — 지금(NumPy 배열 파일, 정확 계산) vs pgvector(PostgreSQL halfvec + HNSW, 근사).

채택은 수치로(절대 규칙 9 의 정신 — 부품 교체도 대결 뒤). 재는 것:
  - 정확도: 같은 질의에서 HNSW 상위 k 가 정확 계산 상위 k 와 얼마나 겹치나(recall@10·@50)
  - 속도: 질의당 시간(중앙값·95%) — 전체 범위와 사업 하나 범위(문서 id 로 거른 질의)
  - 메모리: 정확 계산은 벡터 배열 전체가 메모리에 있어야 한다(파일 크기), pgvector 는 DB 버퍼만
질의 벡터: 실제 조각 벡터 표본(자기 자신은 뺀다) — 임베딩 서비스 없이 같은 공간의 질의를 만든다.

단계:
  --load    grant_embeddings.npz → 표 grant_vec(chunk_id, doc_id, emb halfvec) (있으면 빠진 것만)
  --index   HNSW 색인(halfvec_ip_ops) 만들기
  --duel    대결 → data/eval/pgvector_duel.json, 한 줄 요약 PGVECTOR_DUEL
실행: env PYTHONPATH=src .venv/bin/python scripts/178_pgvector_duel.py --load --index --duel
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from zzaimy.app import grant_search  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.5f}" for x in v.tolist()) + "]"


def load(db, ids: np.ndarray, vecs: np.ndarray, batch: int = 2000) -> int:
    with db._conn() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"CREATE TABLE IF NOT EXISTS grant_vec (chunk_id BIGINT PRIMARY KEY, doc_id BIGINT, emb halfvec({vecs.shape[1]}))")
        have = {int(r[0]) for r in conn.execute("SELECT chunk_id FROM grant_vec")}
    todo = [i for i, c in enumerate(ids.tolist()) if int(c) not in have]
    print(f"벡터 {len(ids):,} · 이미 {len(have):,} · 넣을 것 {len(todo):,}", flush=True)
    doc_of: dict[int, int] = {}
    with db._conn() as conn:
        for r in conn.execute("SELECT id, doc_id FROM doc_chunks"):
            doc_of[int(r[0])] = int(r[1])
    t0 = time.time()
    for k in range(0, len(todo), batch):
        part = todo[k:k + batch]
        rows = [(int(ids[i]), doc_of.get(int(ids[i])), _vec(vecs[i])) for i in part]
        with db._conn() as conn:
            conn.executemany("INSERT INTO grant_vec (chunk_id, doc_id, emb) VALUES (?, ?, ?::halfvec) ON CONFLICT DO NOTHING", rows)
        if (k // batch) % 50 == 0:
            done = k + len(part)
            print(f"  {done:,}/{len(todo):,} ({time.time() - t0:.0f}초)", flush=True)
    return len(todo)


def index(db) -> float:
    t0 = time.time()
    with db._conn() as conn:
        conn.execute("SET maintenance_work_mem = '8GB'")
        conn.execute("SET max_parallel_maintenance_workers = 4")
        conn.execute("CREATE INDEX IF NOT EXISTS grant_vec_hnsw ON grant_vec USING hnsw (emb halfvec_ip_ops) WITH (m = 16, ef_construction = 64)")
        conn.execute("CREATE INDEX IF NOT EXISTS grant_vec_doc ON grant_vec (doc_id)")
    return time.time() - t0


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def duel(db, ids: np.ndarray, vecs: np.ndarray, n: int, seed: int, ef: int) -> dict:
    rng = np.random.default_rng(seed)
    qs = rng.choice(len(ids), size=n, replace=False)
    pos_doc: dict[int, int] = {}
    with db._conn() as conn:
        for r in conn.execute("SELECT chunk_id, doc_id FROM grant_vec"):
            pos_doc[int(r[0])] = int(r[1]) if r[1] is not None else -1
        size = conn.execute("SELECT pg_size_pretty(pg_total_relation_size('grant_vec'))").fetchone()[0]
    res = {"exact_ms": [], "hnsw_ms": [], "r10": [], "r50": [], "scope_exact_ms": [], "scope_hnsw_ms": [], "scope_r10": []}
    for qi in qs.tolist():
        q = vecs[qi].astype(np.float32)
        me = int(ids[qi])
        t = time.time()
        sims = vecs @ q
        top = np.argpartition(-sims, 51)[:51]
        top = top[np.argsort(-sims[top])]
        exact = [int(ids[i]) for i in top if int(ids[i]) != me][:50]
        res["exact_ms"].append((time.time() - t) * 1000)
        t = time.time()
        with db._conn() as conn:
            conn.execute(f"SET hnsw.ef_search = {ef}")
            got = [int(r[0]) for r in conn.execute(
                "SELECT chunk_id FROM grant_vec ORDER BY emb <#> ?::halfvec LIMIT 51", (_vec(q),))]
        got = [c for c in got if c != me][:50]
        res["hnsw_ms"].append((time.time() - t) * 1000)
        res["r10"].append(len(set(got[:10]) & set(exact[:10])) / 10)
        res["r50"].append(len(set(got) & set(exact)) / 50)
        # 사업 하나 범위 — 그 조각 문서의 이웃 문서 묶음(같은 사업 문서 대용: 질의 조각이 속한 사업 문서들)
        did = pos_doc.get(me, -1)
        with db._conn() as conn:
            prog = conn.execute("SELECT src FROM kg_edges WHERE dst = ? AND kind = 'contains' LIMIT 1", (f"doc:{did}",)).fetchone()
            docs = []
            if prog:
                docs = [int(str(r[0]).split(":")[1]) for r in conn.execute(
                    "SELECT dst FROM kg_edges WHERE src = ? AND kind = 'contains' AND dst LIKE 'doc:%'", (prog[0],))]
        if len(docs) >= 5:
            scope = grant_search.scoped_chunk_ids(db, set(docs), None)
            t = time.time()
            p = grant_search._positions(ids)
            idx = np.array([p[c] for c in scope if c in p], dtype=np.int64)
            s2 = vecs[idx] @ q
            ex2 = [int(ids[i]) for i in idx[np.argsort(-s2)[:11]] if int(ids[i]) != me][:10]
            res["scope_exact_ms"].append((time.time() - t) * 1000)
            t = time.time()
            with db._conn() as conn:
                conn.execute(f"SET hnsw.ef_search = {ef}")
                conn.execute("SET hnsw.iterative_scan = relaxed_order")
                ph = ",".join("?" * len(docs))
                g2 = [int(r[0]) for r in conn.execute(
                    f"SELECT chunk_id FROM grant_vec WHERE doc_id IN ({ph}) ORDER BY emb <#> ?::halfvec LIMIT 11", (*docs, _vec(q)))]
            g2 = [c for c in g2 if c != me][:10]
            res["scope_hnsw_ms"].append((time.time() - t) * 1000)
            res["scope_r10"].append(len(set(g2) & set(ex2)) / max(len(ex2), 1))
    summ = {"n": n, "ef_search": ef, "table_size": size, "array_bytes": int(vecs.nbytes),
            "recall@10": round(statistics.mean(res["r10"]), 3), "recall@50": round(statistics.mean(res["r50"]), 3),
            "exact_ms_p50": round(_pct(res["exact_ms"], .5)), "exact_ms_p95": round(_pct(res["exact_ms"], .95)),
            "hnsw_ms_p50": round(_pct(res["hnsw_ms"], .5)), "hnsw_ms_p95": round(_pct(res["hnsw_ms"], .95))}
    if res["scope_r10"]:
        summ.update({"scope_n": len(res["scope_r10"]), "scope_recall@10": round(statistics.mean(res["scope_r10"]), 3),
                     "scope_exact_ms_p50": round(_pct(res["scope_exact_ms"], .5)), "scope_hnsw_ms_p50": round(_pct(res["scope_hnsw_ms"], .5))})
    return summ


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--duel", action="store_true")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ef", type=int, default=100)
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    ids, vecs = grant_search._read(grant_search.INDEX)
    print(f"색인 {grant_search.INDEX} · {vecs.shape} {vecs.dtype}", flush=True)
    if args.load:
        load(db, ids, vecs)
    if args.index:
        print(f"HNSW 색인 {index(db):.0f}초", flush=True)
    if args.duel:
        out = duel(db, ids, vecs, args.n, args.seed, args.ef)
        dst = ROOT / "data/eval/pgvector_duel.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("PGVECTOR_DUEL " + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
