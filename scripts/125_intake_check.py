#!/usr/bin/env python3
"""반입 경로 자가 점검 — 같은 파일을 실제 반입 경로로 임시 문서함에 들여서 결과를 본다.

왜: 이름 규칙·판독 경로를 고친 뒤 스크립트로 소급 적용한 것은 증명이 아니다. 앞으로 들어올 문서가
같은 코드로 저절로 처리되는지는 실제 반입(파싱 → 이름 → 검토 → 조각)을 다시 돌려 봐야 안다.
운영 문서함은 건드리지 않는다 — 임시 DB 에 들이고 결과만 찍는다. 모델(판독·검토)은 실제 것을 쓴다.

사용 (VM):
  env PYTHONPATH=src .venv/bin/python scripts/125_intake_check.py --ids 416,447,364,473,474
  env PYTHONPATH=src .venv/bin/python scripts/125_intake_check.py --files data/scraped/uniall/붙임1*.hwpx
  --twice ID  : 같은 파일을 두 번 들여 중복 차단을 본다
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--scratch", default=str(ROOT / "data" / "tmp" / "intake_check.db"))
    ap.add_argument("--ids", default="", help="운영 문서 id — 올라온 파일 이름과 원본 파일을 그대로 쓴다")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--twice", type=int, default=0, help="이 id 의 파일을 두 번 들인다")
    args = ap.parse_args()

    from zzaimy.generate import llm_connections
    llm_connections.configure(Path(args.db).parent / "llm_connections.json") \
        if hasattr(llm_connections, "configure") else None
    from zzaimy.app.pipeline import DocumentProcessor

    real = Database(Path(args.db))
    scratch_path = Path(args.scratch)
    if scratch_path.exists():
        scratch_path.unlink()
    scratch_path.parent.mkdir(parents=True, exist_ok=True)
    scratch = Database(scratch_path)
    proc = DocumentProcessor()

    plan: list[tuple[str, Path, str]] = []
    ids = [int(x) for x in args.ids.split(",") if x.strip()]
    if args.twice:
        ids += [args.twice, args.twice]
    for i in ids:
        d = real.get_document(i)
        if not d:
            print(f"  #{i} 없음"); continue
        ident = real.get_doc_identity(i)
        orig = ident.get("original_filename") or d["filename"]
        plan.append((orig, Path(d["stored_path"]), d.get("doc_type") or "regulation"))
    for f in args.files:
        plan.append((Path(f).name, Path(f), "regulation"))

    print(f"실제 반입 경로로 {len(plan)}건을 임시 문서함({scratch_path.name})에 들입니다")
    for orig, path, doc_type in plan:
        if not path.exists():
            print(f"  원본 없음: {path}"); continue
        t0 = time.time()
        doc_id = scratch.add_document(filename=orig, stored_path=str(path), doc_type=doc_type)
        try:
            proc.process(scratch, doc_id, path)
        except Exception as e:                       # 반입이 던지면 그것도 결과다
            scratch.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
        d = scratch.get_document(doc_id) or {}
        chunks = [c for c in scratch.list_regulation_chunks() if c["doc_id"] == doc_id]
        titles = {c["reg_title"] for c in chunks}
        print(f"\n  들어온 이름: {orig[:70]}")
        print(f"  → 이름: {d.get('filename')}")
        print(f"    상태 {d.get('status')} · 조각 {len(chunks)}개 · 기준명 {sorted(titles)[:1]} · {time.time() - t0:.0f}초")
        if d.get("parse_note"):
            print(f"    처리: {d['parse_note'][:90]}")
        if d.get("error"):
            print(f"    사유: {d['error'][:90]}")
        head = (d.get("masked_text") or "")[:100].replace("\n", " ")
        if head:
            print(f"    본문 머리: {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
