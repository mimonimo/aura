#!/usr/bin/env python3
"""kordoc 대 우리 파서 대조 — 문서함의 한글 문서를 두 길로 읽어 시간·글자·표·그림을 나란히 (ADR-0033 채택·갱신 근거).

  실행(VM): env PYTHONPATH=src .venv/bin/python scripts/147_kordoc_parity.py [--kinds hwp,hwpx] [--ids 562]
판정: 글자 비율이 0.9~1.3, 표·그림 수가 우리 이상이면 OK, 아니면 WARN. kordoc 판을 올릴 때마다 돌린다.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402


def _measure(parsed) -> dict:
    chars = sum(len(re.sub(r"\s+", "", e.text)) for e in parsed.entries if e.kind in ("text", "heading"))
    tchars = sum(len(re.sub(r"\s+", "", c.text)) for t in parsed.tables for c in t.cells)
    return {"chars": chars + tchars, "tables": len(parsed.tables), "images": len(parsed.images)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--kinds", default="hwp")
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    from zzaimy.ingest.parsers import kordoc
    from zzaimy.ingest.parsers.hwp5 import Hwp5Parser
    from zzaimy.ingest.parsers.hwpx import HwpxParser

    if not kordoc.available():
        print("kordoc 이 없다"); return 2
    print(f"kordoc {kordoc.version()}")
    db = Database(Path(args.db))
    kinds = {"." + k.strip() for k in args.kinds.split(",")}
    docs = [d for d in db.list_documents() if Path(d.get("stored_path") or "").suffix.lower() in kinds]
    if args.ids:
        want = {int(x) for x in args.ids.split(",")}; docs = [d for d in docs if d["id"] in want]
    warn = 0
    print(f"{'id':>4} {'형식':4} {'우리 초':>6} {'kordoc 초':>9} {'글자 우리/kordoc':>16} {'표':>9} {'그림':>7}  판정  파일")
    for d in docs:
        path = Path(d["stored_path"])
        if not path.exists():
            continue
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            t0 = time.time()
            try:
                ours = (Hwp5Parser() if path.suffix.lower() == ".hwp" else HwpxParser()).parse(path, work_dir=Path(t1))
                a = _measure(ours); ta = time.time() - t0
            except Exception as e:
                a = {"chars": 0, "tables": 0, "images": 0}; ta = time.time() - t0; print("  우리 실패:", type(e).__name__)
            t0 = time.time()
            try:
                theirs = kordoc.KordocParser().parse(path, work_dir=Path(t2))
                b = _measure(theirs); tb = time.time() - t0
            except Exception as e:
                print(f"{d['id']:>4} kordoc 실패: {type(e).__name__}: {str(e)[:80]}"); warn += 1; continue
        ratio = b["chars"] / a["chars"] if a["chars"] else 1.0
        level = "OK" if 0.9 <= ratio <= 1.3 and b["tables"] >= a["tables"] * 0.9 and b["images"] >= a["images"] * 0.9 else "WARN"
        warn += level == "WARN"
        print(f"{d['id']:>4} {path.suffix[1:]:4} {ta:>6.0f} {tb:>9.1f} {a['chars']:>7}/{b['chars']:<8} {a['tables']:>4}/{b['tables']:<4} {a['images']:>3}/{b['images']:<3}  {level}  {d['filename'][:40]}")
    print(f"문서 {len(docs)}건 · WARN {warn}")
    return 1 if warn else 0


if __name__ == "__main__":
    raise SystemExit(main())
