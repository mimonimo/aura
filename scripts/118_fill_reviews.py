#!/usr/bin/env python3
"""검토 의견이 비어 있는 문서를 채운다 — 반입 때 모델이 오르내려 놓친 것들.

반입은 색인·분류를 먼저 끝내고 검토 의견을 붙인다. 서빙 장비가 용도마다 다른 모델을 쓰면
그 사이에 호출이 떨어질 수 있는데, 그때 문서를 버리지 않고 '검토 대기'로 남긴다.
이 스크립트가 그 문서들만 다시 부른다.

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/118_fill_reviews.py            # 몇 건인지만
  env PYTHONPATH=src .venv/bin/python scripts/118_fill_reviews.py --apply
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.pipeline import DocumentProcessor  # noqa: E402

WAITING = "검토 의견 생성 대기"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Database(Path(args.db))
    todo = [d for d in db.list_documents()
            if WAITING in (d.get("ai_review") or "") or not (d.get("ai_review") or "").strip()]
    todo = [d for d in todo if (d.get("masked_text") or "").strip()]
    print(f"검토 의견이 비어 있는 문서 {len(todo)}건")
    if not args.apply or not todo:
        if todo:
            print("채우려면 --apply 를 붙이십시오.")
        return 0

    proc = DocumentProcessor()
    ok = fail = 0
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        text = d.get("masked_text") or ""
        review = proc._review_with_retry(d["id"], text, d.get("doc_type") or "auto")
        if WAITING in review:
            fail += 1
        else:
            db.update_document(d["id"], ai_review=review)
            ok += 1
        if i % 5 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} · 채움 {ok} 실패 {fail} · {(time.time() - t0) / 60:.1f}분", flush=True)
    print(f"끝 — 채움 {ok} · 실패 {fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
