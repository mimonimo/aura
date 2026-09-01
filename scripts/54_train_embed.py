"""ZZAIMY-Embed v0.0 시험 파인튜닝 — 축 B 사이클 리허설.

합성 질의(51)로 KURE-v1을 대조학습(MNR)하고, 문서 단위로 분리한 홀드아웃
질의에서 베이스 대비 개선폭을 잰다. 규정 코퍼스 리허설이므로 성과 주장용이
아니라 파이프라인 검증용이다 (정식 학습은 P5, 파일럿 코퍼스로).

순서 규칙 준수: 베이스라인(53) 측정 후 학습. 홀드아웃은 규정 문서(reg_title)
단위로 분리해 같은 문서의 질의가 학습·평가 양쪽에 가지 않는다.

실행(Spark): ZZAIMY_EMBED_DEVICE=cpu .venv-train/bin/python scripts/54_train_embed.py
"""

from __future__ import annotations

import json
import os

# CPU 학습이 전 코어를 잡으면 sshd까지 굶는다(실측 사고) — 스레드를 제한한다
os.environ.setdefault("OMP_NUM_THREADS", "10")
os.environ.setdefault("MKL_NUM_THREADS", "10")
import sqlite3
from datetime import date
from pathlib import Path

BASE_MODEL = "nlpai-lab/KURE-v1"
DB = "data/platform/platform.db"
QUERIES = Path("data/interim/synth_queries.jsonl")
OUT_DIR = Path("models/zzaimy-embed-v0")
REPORT = Path("docs/embed-v0-report.md")
HOLDOUT_RATIO = 0.2
# 2026-09-02 새벽 2에폭(220스텝) 실행이 두 번 모두 후반부에서 SIGKILL —
# 장시간 CPU 학습 중 메모리 증가 정황. 리허설 목적상 1에폭이면 충분하다.
EPOCHS = int(os.environ.get("ZZAIMY_EMBED_EPOCHS", "1"))
BATCH = 16
TOP_K = 10


def load_data():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    chunks = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM regulation_chunks")}
    rows = [json.loads(x) for x in QUERIES.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["chunk_id"] in chunks]
    # 문서 단위 홀드아웃 분리 (결정적: 제목 정렬 후 앞쪽 20%)
    titles = sorted({chunks[r["chunk_id"]]["reg_title"] for r in rows})
    n_hold = max(1, int(len(titles) * HOLDOUT_RATIO))
    holdout_titles = set(titles[:n_hold])
    train, hold = [], []
    for r in rows:
        (hold if chunks[r["chunk_id"]]["reg_title"] in holdout_titles else train).append(r)
    return chunks, train, hold, holdout_titles


def chunk_text(c: dict) -> str:
    return f"{c['reg_title']} {c['heading']}\n{c['content'][:1200]}"


def eval_model(model, chunks: dict, hold_rows: list[dict]) -> dict[str, float]:
    import numpy as np

    ids = list(chunks.keys())
    vecs = model.encode(
        [chunk_text(chunks[i]) for i in ids], batch_size=16, normalize_embeddings=True
    )
    id_pos = {cid: i for i, cid in enumerate(ids)}
    m = {"r1": 0.0, "r5": 0.0, "r10": 0.0, "mrr": 0.0}
    n = 0
    for r in hold_rows:
        for qt in ("practical", "requirement", "keyword"):
            q = (r.get(qt) or "").strip()
            if not q:
                continue
            qv = model.encode([q], normalize_embeddings=True)[0]
            sims = vecs @ qv
            order = np.argsort(sims)[-TOP_K:][::-1]
            n += 1
            gold_pos = id_pos[r["chunk_id"]]
            ranked = list(order)
            if gold_pos in ranked:
                rank = ranked.index(gold_pos) + 1
                m["mrr"] += 1.0 / rank
                if rank <= 1:
                    m["r1"] += 1
                if rank <= 5:
                    m["r5"] += 1
                m["r10"] += 1
    return {k: v / max(n, 1) for k, v in m.items()} | {"n": n}


def main() -> None:
    # 메모리 증가 추적 — SIGKILL 사후 분석용 (60초마다 RSS를 남긴다)
    import threading

    from datasets import Dataset
    from sentence_transformers import (
        SentenceTransformer,
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
    )
    from sentence_transformers.losses import MultipleNegativesRankingLoss

    def _rss_logger() -> None:
        import time as _t

        while True:
            try:
                with open("/proc/self/status") as fh:
                    for ln in fh:
                        if ln.startswith("VmRSS"):
                            print(f"[rss] {ln.split()[1]} kB", flush=True)
                            break
            except OSError:
                return
            _t.sleep(60)

    threading.Thread(target=_rss_logger, daemon=True).start()

    device = os.environ.get("ZZAIMY_EMBED_DEVICE", "cpu")
    chunks, train, hold, holdout_titles = load_data()
    print(f"학습 질의 소스 {len(train)}조각 / 홀드아웃 {len(hold)}조각", flush=True)
    print(f"홀드아웃 문서: {sorted(holdout_titles)}", flush=True)

    pairs = {"anchor": [], "positive": []}
    for r in train:
        pos = chunk_text(chunks[r["chunk_id"]])
        for qt in ("practical", "requirement", "keyword"):
            q = (r.get(qt) or "").strip()
            if q:
                pairs["anchor"].append(q)
                pairs["positive"].append(pos)
    print(f"학습 쌍 {len(pairs['anchor'])}건, device={device}", flush=True)
    if not pairs["anchor"] or not hold:
        raise SystemExit(
            "학습 쌍 또는 홀드아웃이 0건 — 합성 질의(51)가 현재 규정 조각과 맞지"
            " 않는다. 코퍼스 재구축 후에는 51부터 다시 돌려야 한다."
        )

    base = SentenceTransformer(BASE_MODEL, device=device)
    print("베이스 홀드아웃 평가 중…", flush=True)
    base_m = eval_model(base, chunks, hold)
    print("베이스:", base_m, flush=True)

    model = SentenceTransformer(BASE_MODEL, device=device)
    trainer = SentenceTransformerTrainer(
        model=model,
        args=SentenceTransformerTrainingArguments(
            output_dir="/tmp/zzaimy-embed-train",
            use_cpu=(device == "cpu"),  # vLLM이 GPU 점유 중 — 트레이너의 GPU 자동 사용 차단
            num_train_epochs=EPOCHS,
            per_device_train_batch_size=BATCH,
            learning_rate=2e-5,
            warmup_ratio=0.1,
            logging_steps=25,
            save_strategy="no",
            report_to="none",
        ),
        train_dataset=Dataset.from_dict(pairs),
        loss=MultipleNegativesRankingLoss(model),
    )
    result = trainer.train()
    print(f"학습 완료 loss={result.training_loss:.4f}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save(str(OUT_DIR))
    print("튜닝 홀드아웃 평가 중…", flush=True)
    tuned_m = eval_model(model, chunks, hold)
    print("튜닝:", tuned_m, flush=True)

    lines = [
        "# ZZAIMY-Embed v0.0 시험 학습 리포트 (리허설)",
        "",
        f"측정일 {date.today().isoformat()} · 베이스 {BASE_MODEL} · MNR {EPOCHS}ep"
        f" batch {BATCH} · 학습 쌍 {len(pairs['anchor'])} · 홀드아웃 질의 {tuned_m['n']}건"
        f" (문서 단위 분리: {', '.join(sorted(holdout_titles))})",
        "",
        "규정 코퍼스 리허설 — 파이프라인 검증용이며 정식 성과 수치가 아니다.",
        "",
        "| 모델 | Recall@1 | Recall@5 | Recall@10 | MRR@10 |",
        "|---|---|---|---|---|",
        f"| KURE-v1 (베이스) | {base_m['r1']:.3f} | {base_m['r5']:.3f} |"
        f" {base_m['r10']:.3f} | {base_m['mrr']:.3f} |",
        f"| ZZAIMY-Embed v0.0 | {tuned_m['r1']:.3f} | {tuned_m['r5']:.3f} |"
        f" {tuned_m['r10']:.3f} | {tuned_m['mrr']:.3f} |",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
