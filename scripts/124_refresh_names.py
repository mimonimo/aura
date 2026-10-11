#!/usr/bin/env python3
"""문서 이름을 지금의 규칙(app/doc_naming)으로 다시 매긴다.

이름 규칙을 고치면 이미 들어온 문서는 옛 이름을 그대로 쓴다. 올라온 파일 이름(identity.original_filename,
없으면 지금 이름)과 본문으로 반입과 같은 함수(db.rename_from_text)를 다시 부른다 — 화면·인용·검색이 같은 이름을 본다.

지키는 것: 접수번호·원본 파일·저장 경로는 건드리지 않는다. 담당자가 직접 붙인 이름(identity.name_source = user)은 건너뛴다.
기본은 미리보기다. --apply 는 먼저 지금 이름·정체 전부를 백업 파일(JSONL)로 남기고, --restore 로 그 파일에서 되돌린다.
본문은 묶음으로 나눠 읽는다(문서 수만 건의 본문을 한 번에 올리지 않는다).

사용 (VM, 저장소 위치에서 .env.local 을 읽은 셸 — set -a; . ./.env.local; set +a):
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py                       # 미리보기(바뀔 건수·보기)
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --apply               # 백업 뒤 적용
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --like '국고'         # 이름에 그 글자가 든 문서만
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --restore data/platform/backups/names-<시각>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_naming import USER_SOURCE, choose_name, naming_meta  # noqa: E402

BATCH = 300


def _ident(raw) -> dict:
    try:
        return json.loads(raw or "{}") or {}
    except (ValueError, TypeError):
        return {}


def _preview_name(db, row: dict) -> str:
    """--apply 와 같은 규칙으로 고른 이름(같은 이름의 구분 말·자리 표시는 적용 때 붙는다)."""
    ident = _ident(row.get("identity"))
    orig = (ident.get("original_filename") or row.get("filename") or "").strip()
    got = choose_name(orig, row.get("masked_text") or "", naming_meta(db, row))
    name = got["name"] or ""
    if got.get("title") and got.get("date"):
        name = f"{got['title']} · {got['date']}"
    return name


def restore(db, path: Path) -> int:
    n = 0
    with db._conn() as conn:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            conn.execute("UPDATE documents SET filename = ?, identity = ? WHERE id = ? AND receipt_no IS NOT DISTINCT FROM ?"
                         if getattr(conn, "dialect", "") == "postgres" else
                         "UPDATE documents SET filename = ?, identity = ? WHERE id = ? AND receipt_no IS ?",
                         (o["filename"], o["identity"], o["id"], o.get("receipt_no")))
            conn.execute("UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ?", (o["filename"], o["id"]))
            n += 1
    print(f"되돌림 {n}건 ← {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--like", default="", help="이 글자가 이름에 든 문서만")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ask", action="store_true", help="본문 규칙이 못 찾고 파일 이름도 빈약하면 모델에게 제목 줄을 짚게 한다(느림)")
    ap.add_argument("--restore", default="", help="--apply 가 남긴 백업 파일에서 이름·정체를 되돌린다")
    ap.add_argument("--show", type=int, default=40, help="미리보기에 보일 건수")
    args = ap.parse_args()

    db = Database(Path(args.db))
    if args.restore:
        return restore(db, Path(args.restore))

    sql = "SELECT id FROM documents"
    params: tuple = ()
    if args.like:
        sql += " WHERE filename LIKE ?"
        params = (f"%{args.like}%",)
    with db._conn() as conn:
        ids = [int(r[0]) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]

    backup = None
    if args.apply:
        bdir = ROOT / "data" / "platform" / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        bpath = bdir / f"names-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        with db._conn() as conn, bpath.open("w", encoding="utf-8") as fh:
            for i in range(0, len(ids), 2000):
                part = ids[i:i + 2000]
                q = "SELECT id, receipt_no, filename, identity FROM documents WHERE id IN (%s)" % ",".join("?" * len(part))
                for r in conn.execute(q, tuple(part)).fetchall():
                    fh.write(json.dumps({"id": r[0], "receipt_no": r[1], "filename": r[2], "identity": r[3]},
                                        ensure_ascii=False) + "\n")
        backup = bpath
        print(f"백업 {len(ids)}건 → {bpath}", flush=True)
        ask = None
        if args.ask:
            from zzaimy.app.pipeline import DocumentProcessor

            ask = DocumentProcessor().ask_review

    changed = skipped_user = 0
    by_source: dict[str, int] = {}
    for i in range(0, len(ids), BATCH):
        part = ids[i:i + BATCH]
        q = ("SELECT id, filename, identity, masked_text, stored_path, kind, receipt_no FROM documents WHERE id IN (%s) ORDER BY id"
             % ",".join("?" * len(part)))
        with db._conn() as conn:
            rows = [dict(r) for r in conn.execute(q, tuple(part)).fetchall()]
        for r in rows:
            ident = _ident(r["identity"])
            if ident.get("name_source") == USER_SOURCE:
                skipped_user += 1
                continue
            if args.apply:
                db.rename_from_text(r["id"], r["masked_text"] or "", overwrite=True, ask=ask)
                new = (db.get_document(r["id"]) or {}).get("filename", "")
                src = db.get_doc_identity(r["id"]).get("name_source") or "?"
            else:
                new = _preview_name(db, r)
                src = "?"
            if new and new != r["filename"]:
                changed += 1
                by_source[src] = by_source.get(src, 0) + 1
                if changed <= args.show:
                    print(f"  {r['receipt_no'] or r['id']}  {r['filename'][:56]}  →  {new[:56]}")
        print(f"  … {min(i + BATCH, len(ids))}/{len(ids)}", file=sys.stderr, flush=True)
    print(f"{'바꿈' if args.apply else '바뀔 것'} {changed}건 / 대상 {len(ids)}건 · 담당자 이름이라 건너뜀 {skipped_user}건"
          + (f" · 출처 {by_source}" if args.apply else ""))
    if backup:
        print(f"되돌리기: scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --restore {backup.relative_to(ROOT)}")
    else:
        print("미리보기입니다. 적용하려면 --apply 를 붙이십시오(먼저 백업을 남깁니다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
