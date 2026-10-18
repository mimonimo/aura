#!/usr/bin/env python3
"""DGX 원본 목록(scripts/165 의 JSONL, 표준 입력)을 VM 원본 목록 장부(app/archive)로 들인다 — 문서함에 들인 원본은 문서 번호를 잇는다.

사용(운영 PC): ssh dgx '… 165_dgx_inventory.py --root ~/data' | ssh vm 'cd ~/zzaimy-capstone && … 166_load_inventory.py'
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import archive  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    rows = archive.parse_jsonl(sys.stdin.read())
    origins = {}
    led = ROOT / "data" / "platform" / "origins.jsonl"
    if led.is_file():
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                origins[o["origin"]] = int(o["doc_id"])
            except (ValueError, KeyError):
                continue
    got = archive.load(db, rows, origins)
    print(f"원본 목록 {got['rows']}건 들임({got['at']}), 문서함과 이어진 것 {sum(1 for r in rows if r['rel'] in origins)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
