"""처리 실패 원본 치우기 — 점검(175)의 실패 목록(pipeline_failed.jsonl)을 원본 관리 정리함 「처리 실패」 갈래로 옮긴다(10-26).

정리함은 표시만 한다(archive_cleanup) — DGX 원본 파일은 그대로, 보관소 목록·점검 대상·실패 수에서만 빠진다.
되돌리기: --restore (스캔 판독을 다시 돌려 살릴 때 등). 옮긴 뒤 점검(175)을 다시 돌려야 처리 흐름 화면 수치가 바뀐다.
다시 시도: 원본 관리 정리 카드의 「처리 실패」에서 고른 원본(app/read_failure 의 다시 읽기 장부)을 옮기기 전에 먼저 읽는다 —
DGX 원본을 하나씩 받아 이 서버 처리기(판독 모델 포함, 검토 의견 생략)로. 읽으면 문서함에, 못 읽으면 새 사유로 실패 장부에 남아
다음 정리 때 다시 정리함으로 온다. 글자 읽기 맡기기(ocr)는 판독 모델이 닿을 때만 읽고 그 밖에는 장부에 남겨 둔다.
  PYTHONPATH=src .venv/bin/python scripts/217_clean_failed.py [--restore] [--dry-run] [--retry-limit 20]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from zzaimy.app import archive_cleanup, read_failure
from zzaimy.app.db import Database


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/platform")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retry-limit", type=int, default=20, help="한 번에 다시 읽을 원본 수(다시 시도 장부)")
    a = ap.parse_args()
    dd = Path(a.data)
    db = Database(dd / "platform.db")
    pending = read_failure.archive_pending(db)
    if pending and not a.restore and not a.dry_run:
        import os
        from zzaimy.app.pipeline import DocumentProcessor
        os.environ.setdefault("ZZAIMY_LIGHT_PROCESS", "1")        # 검토 의견(27B)은 건너뛴다 — 읽기·조각만(168 과 같은 가벼운 처리)
        got = read_failure.run_archive_retry(db, DocumentProcessor(), limit=a.retry_limit, data_dir=dd)
        print(f"다시 시도 {len(pending)}건 중 읽음 {got['read']} · 다시 실패 {got['failed']} · 판독 대기 {got['waiting']}")
    if a.restore:
        with db._conn() as conn:
            n = conn.execute("DELETE FROM archive_cleanup WHERE category = ?", ("failed",)).rowcount
        archive_cleanup._cache.update(v=None)
        print(f"되돌림 {n}건")
        return
    rows = [json.loads(x) for x in (dd / "pipeline_failed.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    retrying = set(read_failure.archive_pending(db))               # 다시 읽기 장부에 남은 것은 옮기지 않는다
    rels = sorted({r["rel"] for r in rows if r.get("rel") and r["rel"] not in retrying})
    why: dict[str, int] = {}
    for r in rows:
        why[r.get("reason") or "?"] = why.get(r.get("reason") or "?", 0) + 1
    print(f"실패 목록 {len(rels)}건 · " + " · ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda x: -x[1])))
    if a.dry_run:
        return
    archive_cleanup.put(db, rels, "failed", by="scripts/217")
    print(f"정리함으로 {len(rels)}건")


if __name__ == "__main__":
    main()
