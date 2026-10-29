#!/usr/bin/env python3
"""화면별 SQL 시간 — 한 화면을 열 때 보낸 SQL 문을 시간 순으로 보인다(빠진 색인 찾기, 10-28).


사용(VM, 운영 폴더에서 .env.local 과 함께):
  cd ~/zzaimy-capstone && set -a && . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/222_sql_timing.py /주소 …
"""
import sys, time, threading
from pathlib import Path
_o=threading.Thread.start
threading.Thread.start=lambda self: None if self.name in {"auto-reindex","drive-share-sweep","drive-share","weekly-schedule","org-reconcile","requeue-stalled","warm-nouns","warm-pages"} else _o(self)
from zzaimy.app import database_backend as dbb
log=[]
orig=dbb.PostgresConnection.execute
def ex(self, st, params=()):
    t=time.monotonic(); r=orig(self, st, params); log.append((time.monotonic()-t, st[:160].replace("\n"," "), len(params) if hasattr(params,'__len__') else 0)); return r
dbb.PostgresConnection.execute=ex
from fastapi.testclient import TestClient
from zzaimy.app.main import create_app
from zzaimy.app.drafter import SliceDrafter
from zzaimy.app.pipeline import DocumentProcessor
app=create_app(db_path=Path("data/platform/platform.db"),inbox_dir=Path("data/platform/inbox"),processor=DocumentProcessor(),drafter=SliceDrafter(),responder=None,password=None)
c=TestClient(app)
for p in sys.argv[1:]:
    c.get(p); log.clear(); t=time.monotonic(); c.get(p); tot=time.monotonic()-t
    print(f"== {p} {tot:.2f}s, SQL {sum(x[0] for x in log):.2f}s in {len(log)}")
    for d, st, n in sorted(log, reverse=True)[:6]: print(f"   {d:.3f} n={n} {st}")
