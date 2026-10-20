#!/usr/bin/env python3
"""처리 실패 문서 정리 — 문서함에 「처리 실패」 로 남은 문서를 지금 읽기 도구로 한 번 더 읽어 보고, 그래도 안 되면 문서함에서 뺀다.

왜(10-21 사용자): 실패한 문서가 문서함에 계속 남아 있으면 목록·수치가 지저분하고 아무도 처리하지 않는다. 읽기 도구(kordoc·MinerU 등)가
나아졌으면 다시 읽어 살리고, 그래도 못 읽으면(암호·손상·빈 파일) 문서함에서 뺀다. 원본은 원본 보관소(DGX)에 그대로 남는다.
- 내용(파일 지문)이 같은 처리 완료 문서가 있으면 다시 읽지 않고 바로 뺀다(중복 사본). 이름만 같은 것은 다른 문서일 수 있어 다시 읽는다.
- 뺀 문서는 data/platform/failed_dropped.jsonl 에 남긴다(이름·저장 위치·오류·시각) — 되살리려면 원본을 다시 반입한다.
- 처리된 지 하루가 안 된 실패는 건드리지 않는다(담당자가 막 올린 문서를 바로 지우지 않게).

사용(VM, zz_reconcile.sh 가 하루 한 번): PYTHONPATH=src .venv/bin/python scripts/199_settle_failed.py [--dry]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import storage  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="무엇을 할지 보이기만")
    ap.add_argument("--min-age-hours", type=float, default=24)
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    cut = (datetime.now() - timedelta(hours=args.min_age_hours)).isoformat(timespec="seconds")
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, filename, stored_path, error, created_at, content_sha256 FROM documents WHERE status = 'failed' AND created_at < ?"
            " ORDER BY id", (cut,)).fetchall()]
        shas = [r["content_sha256"] for r in rows if r.get("content_sha256")]
        done_sha = {x[0] for x in conn.execute(
            f"SELECT content_sha256 FROM documents WHERE status = 'reviewed' AND content_sha256 IN ({','.join('?' * len(shas))})",
            shas).fetchall()} if shas else set()
    if not rows:
        print("처리 실패 문서 없음")
        return 0
    proc = None
    saved, dropped = [], []
    for r in rows:
        why = ""
        if r.get("content_sha256") and r["content_sha256"] in done_sha:
            why = "내용이 같은 처리 완료 문서가 있음(중복 사본)"
        else:
            path = Path(r["stored_path"] or "")
            if not path.is_absolute():
                path = storage.root(Path(db.path).parent) / path
            if str(r["stored_path"] or "").startswith("dgx://") or not path.is_file():
                why = "플랫폼에 원본 파일이 없음"
            elif args.dry:
                why = "(다시 읽어 볼 대상)"
            else:
                if proc is None:
                    from zzaimy.app.pipeline import DocumentProcessor
                    proc = DocumentProcessor()
                try:
                    proc.process(db, int(r["id"]), path)
                except Exception as e:
                    why = f"다시 읽기 실패: {type(e).__name__}"
                d = db.get_document(int(r["id"]))
                if d and d.get("status") == "reviewed":
                    saved.append(r)
                    print(f"살림  doc {r['id']} 「{r['filename']}」", flush=True)
                    continue
                why = why or f"다시 읽어도 실패: {(d or {}).get('error') or r['error'] or ''}"[:160]
        print(f"{'뺄 것' if args.dry else '뺌'}  doc {r['id']} 「{r['filename']}」 — {why}", flush=True)
        if args.dry and why == "(다시 읽어 볼 대상)":
            continue
        dropped.append({**r, "why": why})
        if not args.dry:
            doc = db.get_document(int(r["id"]))
            if doc:
                db.delete_document(int(r["id"]))
                storage.remove_intake_dir(db, doc)
    if dropped and not args.dry:
        with (Path(db.path).parent / "failed_dropped.jsonl").open("a", encoding="utf-8") as f:
            for r in dropped:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "doc_id": r["id"], "filename": r["filename"],
                                    "stored_path": r["stored_path"], "error": (r.get("error") or "")[:300], "why": r["why"]},
                                   ensure_ascii=False) + "\n")
    print(f"처리 실패 정리: 살림 {len(saved)} · 뺌 {0 if args.dry else len(dropped)}" + (" (미리 보기)" if args.dry else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
