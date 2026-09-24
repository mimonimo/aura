#!/usr/bin/env python3
"""접수 문서의 그림 쪽(글자층 없는 쪽)을 비전 모델로 판독해 조각으로 더한다 — 운영 서비스 밖에서 한 번에 여러 묶음.

플랫폼의 요청 시 판독(POST /doc/{id}/read-pages, 12쪽씩)과 같은 코드(read_image_pages)를 쓴다. 서비스 재시작이 잦은 날
긴 판독을 서비스 배경 작업에 맡기면 끊기므로(실측 2026-09-24) 이 스크립트로 돌린다. 마스킹 대상 문서면 가린다.

실행(VM): set -a; . .env.local; set +a; env PYTHONPATH=src .venv/bin/python scripts/143_read_image_pages.py --doc 565 [--rounds 6] [--batch 12]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.pipeline import DocumentProcessor  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--doc", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--batch", type=int, default=12)
    args = ap.parse_args()
    db = Database(Path(args.db))
    proc = DocumentProcessor.__new__(DocumentProcessor)     # 판독·마스킹만 쓴다 — 파서 초기화는 생략
    proc._masker = None
    total = 0
    for r in range(1, args.rounds + 1):
        t0 = time.time()
        res = proc.read_image_pages(db, args.doc, max_pages=args.batch)
        n = int(res.get("read") or 0)
        total += n
        print(f"[{r}] 판독 {n}쪽 {res.get('pages')} 조각 {res.get('chunks', 0)} · {time.time() - t0:.0f}초 {res.get('reason') or ''}")
        if n == 0:
            break
    left = [p for p in DocumentProcessor.sparse_pages(Path(db.get_document(args.doc)["stored_path"])) if p not in set(db.vision_pages(args.doc))]
    print(f"합계 {total}쪽 · 남은 그림 쪽 {len(left)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
