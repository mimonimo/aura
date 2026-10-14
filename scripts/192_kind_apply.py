#!/usr/bin/env python3
"""27B 갈래 판정(scripts/191 → kind27b-*.jsonl)을 문서 갈래(documents.kind)에 반영한다(VM).

- DGX 보관 문서(stored_path = dgx://<원본 경로>)만, 판(version)이 지금 원본과 같을 때만.
- 기준 문서(doc_type regulation)와 담당자가 고친 갈래(identity.kind_by_hand)는 건드리지 않는다.
- 바꾸기 전 갈래를 백업 파일(data/platform/backups/kind27b-YYYYMMDD.jsonl)에 덧붙인다 — --restore 로 되돌린다.
- 같은 판정은 다시 적용해도 그대로다(이미 같으면 건너뜀). 바뀐 문서가 있으면 그래프를 다시 짓게 .kg-dirty 를 남긴다.
사용(VM): env PYTHONPATH=src .venv/bin/python scripts/192_kind_apply.py data/inbox/parsed/kind27b-0.jsonl [--apply]
          env PYTHONPATH=src .venv/bin/python scripts/192_kind_apply.py --restore data/platform/backups/kind27b-20261014.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_routing import KINDS  # noqa: E402


def _db() -> Database:
    return Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))


def load(files: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in files:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("rel") and d.get("kind") in KINDS:
                out[d["rel"]] = d                                   # 같은 원본은 마지막 판정
    return out


def plan(db: Database, judged: dict[str, dict]) -> list[tuple[int, str, str]]:
    """[(문서, 지금 갈래, 새 갈래)] — 바뀔 것만."""
    changes = []
    with db._conn() as conn:
        rows = conn.execute("SELECT id, stored_path, kind, doc_type, identity FROM documents WHERE stored_path LIKE 'dgx://%'").fetchall()
        # 원본의 지금 판 — 167 의 version 과 같은 꼴(크기:시각)
        ver = {r[0]: f"{int(r[1] or 0)}:{int(float(r[2] or 0))}"
               for r in conn.execute("SELECT rel, size, mtime FROM archive_files WHERE removed_at = ''")}
    for did, sp, kind, dtype, ident in rows:
        rel = str(sp)[len("dgx://"):]
        j = judged.get(rel)
        if not j or dtype == "regulation" or (kind or "") == j["kind"]:
            continue
        try:
            if json.loads(ident or "{}").get("kind_by_hand"):
                continue
        except ValueError:
            pass
        if j.get("version") and rel in ver and ver[rel] != j["version"]:
            continue                                                # 원본이 바뀐 뒤의 옛 판정
        changes.append((int(did), kind or "", j["kind"]))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", default="")
    args = ap.parse_args()
    db = _db()
    if args.restore:
        rows = [json.loads(x) for x in Path(args.restore).read_text(encoding="utf-8").splitlines() if x.strip()]
        with db._conn() as conn:
            for r in rows:
                conn.execute("UPDATE documents SET kind = ? WHERE id = ?", (r["old"], r["id"]))
        print(f"되돌림 {len(rows)}건")
        return 0
    changes = plan(db, load(args.files))
    print(f"KIND27B_APPLY 바뀔 문서 {len(changes)} · {Counter(f'{a or "-"}→{b}' for _, a, b in changes).most_common(8)}", flush=True)
    if not args.apply or not changes:
        return 0
    bak = ROOT / "data/platform/backups" / f"kind27b-{time.strftime('%Y%m%d')}.jsonl"
    bak.parent.mkdir(parents=True, exist_ok=True)
    with bak.open("a", encoding="utf-8") as fo, db._conn() as conn:
        for did, old, new in changes:
            fo.write(json.dumps({"id": did, "old": old, "new": new, "at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False) + "\n")
            conn.execute("UPDATE documents SET kind = ? WHERE id = ?", (new, did))
    (Path(db.path).parent / ".kg-dirty").touch()
    print(f"  적용 {len(changes)}건 · 되돌리기: scripts/192_kind_apply.py --restore {bak}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
