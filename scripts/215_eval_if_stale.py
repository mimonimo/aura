#!/usr/bin/env python
"""규정 검색 품질 — 구성이 바뀌었으면 다시 잰다(하루 한 번, zz_reconcile.sh).

화면의 「재측정」 버튼 대신: 마지막 측정(retrieval-latest.json)의 규정 조각 수·임베딩 모델이 지금과 다르면 53 을 돌린다.
같으면 아무것도 하지 않는다. 합성 질의 세트가 없거나 이미 측정 중이면 건너뛴다(가짜 수치 없음). 한 번에 약 1시간.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def stale(latest: dict | None, chunks: int, model: str) -> str:
    """다시 잴 까닭(빈 글이면 그대로)."""
    if not latest:
        return "측정 기록 없음"
    if int(latest.get("n_chunks") or 0) != int(chunks):
        return f"규정 조각 {latest.get('n_chunks')} → {chunks}"
    if model and latest.get("embedding_model") and latest["embedding_model"] != model:
        return f"임베딩 {latest['embedding_model']} → {model}"
    return ""


def main() -> int:
    from zzaimy.app import metrics
    from zzaimy.app.db import Database
    from zzaimy.app.embed_search import _index_model_name
    from zzaimy.eval import retrieval_eval as rev

    db_path = ROOT / "data" / "platform" / "platform.db"
    ev = db_path.parent / "eval"
    if not rev.QUERIES_PATH.exists():
        print("건너뜀 — 합성 질의 세트 없음")
        return 0
    if rev.running_state(ev):
        print("건너뜀 — 이미 측정 중")
        return 0
    try:
        latest = json.loads((ev / rev.LATEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        latest = None
    db = Database(db_path)
    why = stale(latest, metrics.live(db)["values"].get("regulation_chunks", 0), _index_model_name() or "")
    if not why:
        print("그대로 — 마지막 측정과 구성이 같음")
        return 0
    print(f"다시 잼 — {why}")
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "53_eval_retrieval.py"), "--db", str(db_path)])


if __name__ == "__main__":
    raise SystemExit(main())
