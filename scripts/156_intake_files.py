#!/usr/bin/env python3
"""고른 파일들을 한 프로젝트로 문서함에 반입한다 — 실문서 파일럿(2026-10-01, ADR-0047: LINC3.0 계획서·보고서 9건).

문서함의 보통 반입과 같은 길이다: 등록 → 원본을 문서 폴더로(adopt_original, ADR-0030) → 처리기(DocumentProcessor:
글·구조 추출, 개인정보 처리, 조각, 맥락 분석). 같은 내용(해시)이 이미 있으면 들이지 않는다. 문서마다 따로 돌리고 제한 시간을 둔다
(큰 한글 파일 하나가 배치를 세우지 못하게, 115 와 같은 방식).

사용(VM, .env.local 을 읽고):
  set -a; . ./.env.local; set +a
  env PYTHONPATH=src .venv/bin/python scripts/156_intake_files.py --project "LINC3.0 (2022~2024)" --sector grant \\
      --owner zzdev data/inbox/linc3/*.hwp data/inbox/linc3/*.hwpx            # 무엇을 할지만
  ... --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402


def _db_path() -> Path:
    return Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data" / "platform" / "platform.db")


def _run_one(doc_id: int, file_path: str) -> None:
    from zzaimy.app.pipeline import DocumentProcessor

    db = Database(_db_path())
    try:
        DocumentProcessor().process(db, doc_id, Path(file_path))
    except Exception as e:
        db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}"[:300])
        raise SystemExit(1)


def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _completed_ok(exitcode: int | None, doc: dict, *, timed_out: bool = False) -> bool:
    """A normal worker exit alone does not mean the pipeline accepted the document."""
    return not timed_out and exitcode == 0 and doc.get("status") == "reviewed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--project", required=True, help="묶을 프로젝트 이름(없으면 만든다)")
    ap.add_argument("--program", default="", help="과거 사업 묶음의 사업 분류 id — 주면 이름이 아니라 이 id 로 묶음을 찾는다")
    ap.add_argument("--archived", action="store_true", help="묶음을 보관 상태로(사이드바에 띄우지 않음, 「보관된 사업」에서 불러온다)")
    ap.add_argument("--sector", default="grant")
    ap.add_argument("--doc-type", default="grant")
    ap.add_argument("--owner", default="zzaimy")
    ap.add_argument("--timeout", type=int, default=40, help="문서 한 건의 제한 시간(분)")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--origin-base", default="", help="이 폴더 아래 상대 경로를 원본 보관소 경로로 장부(data/platform/origins.jsonl)에 적는다")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from zzaimy.app import storage

    db = Database(_db_path())
    files = [Path(f) for f in args.files if Path(f).is_file()]
    known = {}
    with db._conn() as conn:
        for r in conn.execute("SELECT id, stored_path FROM documents").fetchall():
            p = Path(r[1] or "")
            if p.is_file() and p.stat().st_size < 600 * 1024 * 1024:
                known.setdefault(p.stat().st_size, []).append((int(r[0]), p))
    plan = []
    for f in files:
        same = [d for d, p in known.get(f.stat().st_size, []) if _sha1(p) == _sha1(f)]
        plan.append((f, same[0] if same else None))
        print(f"{'이미 있음 #' + str(same[0]) if same else '반입':>10}  {f.stat().st_size / 1e6:8.1f}MB  {f.name}")
    if not args.apply:
        print("미리 보기입니다 — --apply 로 실행")
        return 0
    if args.program:
        project_id = None      # 과거 사업 자료 — 연도별 보관 묶음은 같은 동기화의 맞춤 단계(archive.align_archived_projects)가 장부 연도로 정한다
    else:
        proj = next((p for p in db.list_projects(args.sector) if p["name"] == args.project), None)
        project_id = int(proj["id"]) if proj else db.create_project(args.sector, args.project, owner=args.owner,
                                                                    archived=args.archived)
    print(f"프로젝트 #{project_id} 「{args.project}」")
    # 내용이 같은 문서가 이미 있어 건너뛰는 원본도 그 문서 번호로 원본 경로 장부에 적는다 — 안 적으면 동기화(170)가
    # 회차마다 같은 원본을 「새 파일」로 다시 골라 그래프를 헛되이 다시 짓는다(2026-10-04 실측: 같은 2건이 회차마다)
    if args.origin_base:
        led = ROOT / "data" / "platform" / "origins.jsonl"
        with led.open("a", encoding="utf-8") as fh:
            for f, dup in plan:
                if dup is None:
                    continue
                try:
                    rel = str(f.resolve().relative_to(Path(args.origin_base).resolve()))
                except ValueError:
                    continue
                fh.write(json.dumps({"doc_id": dup, "origin": rel, "at": time.strftime("%Y-%m-%d %H:%M"), "via": "same-content"},
                                    ensure_ascii=False) + "\n")
    todo = [f for f, dup in plan if dup is None]
    running, ok, fail, t0 = [], 0, 0, time.time()
    while todo or running:
        while todo and len(running) < max(1, args.jobs):
            f = todo.pop(0)
            doc_id = db.add_document(filename=f.name, stored_path=str(f), doc_type=args.doc_type, sector=args.sector,
                                     project_id=project_id, owner=args.owner)
            if args.origin_base:
                try:
                    rel = str(f.resolve().relative_to(Path(args.origin_base).resolve()))
                except ValueError:
                    rel = ""
                if rel:
                    led = ROOT / "data" / "platform" / "origins.jsonl"
                    with led.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"doc_id": doc_id, "origin": rel, "at": time.strftime("%Y-%m-%d %H:%M")},
                                            ensure_ascii=False) + "\n")
            stored = storage.adopt_original(db, doc_id, f)
            child = mp.Process(target=_run_one, args=(doc_id, str(stored)))
            child.start()
            running.append({"doc_id": doc_id, "proc": child, "t0": time.time(), "name": f.name})
            print(f"  시작 #{doc_id} {f.name}", flush=True)
        time.sleep(3)
        for job in list(running):
            child = job["proc"]
            over = time.time() - job["t0"] > args.timeout * 60
            if child.is_alive() and not over:
                continue
            timed_out = child.is_alive()
            if timed_out:
                child.terminate()
                child.join(10)
                db.update_document(job["doc_id"], status="failed", error=f"시간 초과 — {args.timeout}분")
            doc = db.get_document(job["doc_id"]) or {}
            if _completed_ok(child.exitcode, doc, timed_out=timed_out):
                ok += 1
            else:
                fail += 1
            print(f"  끝 #{job['doc_id']} {doc.get('status')} {(time.time() - job['t0']) / 60:.1f}분 {job['name']}"
                  + (f" — {doc.get('error')}" if doc.get("status") == "failed" else ""), flush=True)
            running.remove(job)
    print(f"반입 완료 — 성공 {ok} 실패 {fail} ({(time.time() - t0) / 60:.1f}분). 다음: 색인(scripts/96 --apply)·개체 그래프")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
