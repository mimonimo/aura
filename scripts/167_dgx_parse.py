#!/usr/bin/env python3
"""DGX 원본 보관소의 문서를 DGX 에서 가볍게 처리 — 문서함과 같은 처리기(DocumentProcessor)로 파싱·조각까지만(검토 의견 LLM·비전 판독 없이).

사용자 2026-10-02: "해당 부분 분석한 자료라던지 이런건 VM 에도 다 있어야 … RAG 나 온톨로지, 문서 분류 이런거, 문서함에도 떠야".
원본은 DGX 에 두고(ADR-0047), 결과(글·조각·분류)만 JSONL 로 낸다 → VM 에서 scripts/168 이 'DGX 보관 문서'로 문서함에 들인다.

입력: scripts/165 원본 목록(JSONL)과 이미 들인 원본 경로 목록(--skip, 한 줄에 하나).
대상: 글이 있는 형식(hwp·hwpx·pdf·docx·xlsx), 이름·크기 중복 아님, 이미 들이지 않음. 작업자마다 따로 SQLite 를 임시로 쓴다.
출력: --out 디렉터리에 작업자별 parsed-<n>.jsonl (한 줄 = 문서 하나: rel·분류·글·조각)

사용(DGX): ZZAIMY_LIGHT_PROCESS=1 PYTHONPATH=src .venv-parse/bin/python scripts/167_dgx_parse.py \\
           --inventory ~/archive_inventory.jsonl --skip ~/ingested.txt --out ~/parsed --workers 12 [--limit 500]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TEXT_EXT = {"hwp", "hwpx", "pdf", "docx", "xlsx"}
MAX_BYTES = 300 * 1024 * 1024


def worker(n: int, items: list[dict], data_root: str, out_dir: str, timeout_s: int) -> None:
    os.environ["ZZAIMY_LIGHT_PROCESS"] = "1"
    os.environ.setdefault("ZZAIMY_NO_VISION", "1")
    work = Path(out_dir) / f"w{n}"
    work.mkdir(parents=True, exist_ok=True)
    os.environ["ZZAIMY_PLATFORM_SQLITE_PATH"] = str(work / "tmp.db")
    os.environ.pop("ZZAIMY_DATABASE_URL", None)
    from zzaimy.app.db import Database
    from zzaimy.app.pipeline import DocumentProcessor

    db = Database(work / "tmp.db")
    proc = DocumentProcessor()
    out = (Path(out_dir) / f"parsed-{n}.jsonl").open("a", encoding="utf-8")
    for it in items:
        src = Path(data_root) / it["rel"]
        t0 = time.time()
        rec = {k: it.get(k) for k in ("rel", "program", "program_name", "status", "kind", "year", "round", "size", "mtime")}
        try:
            did = db.add_document(filename=src.name, stored_path=str(src), doc_type="grant", sector="grant")
            proc.process(db, did, src)
            d = db.get_document(did) or {}
            rec.update({"ok": d.get("status") == "reviewed", "error": d.get("error"), "filename": d.get("filename") or src.name,
                        "parse_note": d.get("parse_note"), "masked_text": (d.get("masked_text") or "")[:400000],
                        "doc_kind": d.get("kind"), "family": d.get("family"),
                        "chunks": [{k: c.get(k) for k in ("seq", "kind", "content", "page_no", "bbox")} for c in db.list_doc_chunks(did)]})
        except Exception as e:  # 한 문서 실패가 작업자를 멈추지 않게
            rec.update({"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "chunks": []})
        rec["sec"] = round(time.time() - t0, 1)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        if rec["sec"] > timeout_s:
            print(f"[w{n}] 느림 {rec['sec']}초 {it['rel'][-60:]}", flush=True)
    out.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--skip", default="")
    ap.add_argument("--root", default=str(Path.home() / "data"))
    ap.add_argument("--out", default=str(Path.home() / "parsed"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ext", default=",".join(sorted(TEXT_EXT)), help="처리할 형식(쉼표) — 엑셀은 docling 이 있어야 한다")
    ap.add_argument("--retry-failed", action="store_true", help="이전에 실패한 원본도 다시(판독 도구를 새로 깐 뒤). 성공하면 같은 rel 의 새 줄이 덧붙고 168 이 그것을 들인다")
    ap.add_argument("--slow", type=int, default=600, help="이보다 오래 걸린 문서는 로그에 남긴다(초)")
    args = ap.parse_args()
    exts = {e.strip() for e in args.ext.split(",") if e.strip()}
    skip = set(Path(args.skip).read_text(encoding="utf-8").splitlines()) if args.skip and Path(args.skip).is_file() else set()
    done = set()
    for f in Path(args.out).glob("parsed-*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("rel") and (rec.get("ok") or not args.retry_failed):
                done.add(rec["rel"])
    items = []
    for line in Path(args.inventory).read_text(encoding="utf-8").splitlines():
        try:
            it = json.loads(line)
        except ValueError:
            continue
        if it.get("ext") not in exts or it.get("dup_of") or it["rel"] in skip or it["rel"] in done:
            continue
        if int(it.get("size") or 0) > MAX_BYTES or Path(it["rel"]).name.startswith("~$"):
            continue
        items.append(it)
    # 계획·보고·평가·기본계획을 먼저, 큰 파일은 뒤로
    pri = {"evaluation": 0, "plan": 1, "report": 2, "basic_plan": 3, "announcement": 4, "guideline": 5, "regulation": 6, "criteria": 7}
    items.sort(key=lambda it: (pri.get(it.get("kind") or "", 9), int(it.get("size") or 0)))
    if args.limit:
        items = items[: args.limit]
    print(f"대상 {len(items)}건(이미 처리 {len(done)}, 문서함에 있음 {len(skip)}) · 작업자 {args.workers}", flush=True)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    shards = [items[i:: args.workers] for i in range(args.workers)]
    procs = [mp.Process(target=worker, args=(i, s, args.root, args.out, args.slow)) for i, s in enumerate(shards) if s]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    print("PARSE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
