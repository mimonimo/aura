#!/usr/bin/env python3
"""생각 글 새김 점검 — 운영 한글 문서의 절 본문에서 영어 생각 줄(delegate.strip_reasoning 이 거를 줄)을 찾는다(10-28).

걸린 줄이 있으면 문서·절·첫 줄을 보인다. 오탐(코드·경로·이름)을 줄일 때 기준 시험으로도 쓴다.
사용(VM, 운영 폴더에서 .env.local 과 함께):
  cd ~/zzaimy-capstone && set -a && . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/223_reasoning_leak_scan.py 
"""
import re, sys
from pathlib import Path
from zzaimy.app import delegate as dn
from zzaimy.app.db import Database
from zzaimy.app import webhwp_write as ww
db = Database(Path("data/platform/platform.db"))
hits = []
for r in db.list_files(kind="webhwp", limit=20000):
    p = Path(r["path"])
    if not p.exists() or ".휴지통" in str(p) or ".판" in str(p):
        continue
    try:
        data = p.read_bytes(); names = [x["name"] for x in ww.fields(data) if x["name"].startswith("절:")]
    except Exception:
        continue
    bad = []
    for n in names:
        try:
            b = ww.section_body(data, n)
        except Exception:
            continue
        lines = [l for l in b.splitlines() if l.strip() and dn.strip_reasoning(l) == ""]
        if lines:
            bad.append((n, len(lines), lines[0][:90]))
    if bad:
        hits.append((r["id"], r["name"], str(p.parent.name), bad))
print("문서", len(hits))
for h in hits[:30]:
    print(h[0], h[1][:40], h[2], [(b[0][:16], b[1]) for b in h[3]][:4])
    print("    예:", h[3][0][2])
