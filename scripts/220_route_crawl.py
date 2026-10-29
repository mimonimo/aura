#!/usr/bin/env python3
"""화면 전수 점검 — 앱의 GET 주소 전부를 운영 데이터로 두 번씩 열어 오류(500)·느린 곳(두 번째 열기 1초 넘음)을 찾는다(10-28).

읽기만 한다(내려받기·지우기·외부 호출 주소는 건너뜀). 관리자 범위로 연다.
사용(VM, 운영 폴더에서 .env.local 과 함께):
  cd ~/zzaimy-capstone && set -a && . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/220_route_crawl.py 
"""
import re, sys, threading, time
from pathlib import Path
_orig = threading.Thread.start
_SKIP = {"auto-reindex", "drive-share-sweep", "drive-share", "weekly-schedule", "org-reconcile", "requeue-stalled", "warm-nouns"}
threading.Thread.start = lambda self: None if self.name in _SKIP else _orig(self)
from fastapi.testclient import TestClient
from zzaimy.app.main import create_app
from zzaimy.app.drafter import SliceDrafter
from zzaimy.app.pipeline import DocumentProcessor
from zzaimy.app.db import Database
app = create_app(db_path=Path("data/platform/platform.db"), inbox_dir=Path("data/platform/inbox"),
                 processor=DocumentProcessor(), drafter=SliceDrafter(), responder=None, password=None)
db = Database(Path("data/platform/platform.db"))
with db._conn() as c:
    one = lambda q: (c.execute(q).fetchone() or [0])[0]
    ids = {"doc": one("select max(id) from documents where status='reviewed'"), "project": one("select max(id) from projects where archived=0"),
           "session": one("select max(id) from chat_sessions where channel='chat'"), "fid": one("select max(id) from files where kind='webhwp'")}
SAMPLE = {"doc_id": ids["doc"], "id": ids["doc"], "project_id": ids["project"], "pid": ids["project"], "session_id": ids["session"],
          "sid": ids["session"], "fid": ids["fid"], "file_id": ids["fid"]}
SKIPPATH = re.compile(r"logout|export|download|\.jsonl|\.csv|\.zip|/raw|/original|callback|view-as|/cmd|/events|/stream|/sse|/static|delete|/wasm|/webhwpctrl", re.I)
c = TestClient(app, raise_server_exceptions=False)
def walk(routes, prefix=""):
    for r in routes:
        orig = getattr(r, "original_router", None)
        if orig is not None:
            ctx = getattr(r, "include_context", None)
            pre = prefix + (getattr(ctx, "prefix", "") or "")
            yield from walk(orig.routes, pre)
        else:
            yield prefix, r
rows = []
for prefix, r in walk(app.routes):
    methods = getattr(r, "methods", set()) or set()
    path = prefix + getattr(r, "path", "")
    if "GET" not in methods or SKIPPATH.search(path):
        continue
    params = re.findall(r"{(\w+)(?::\w+)?}", path)
    if any(p not in SAMPLE for p in params):
        continue
    url = re.sub(r"{(\w+)(?::\w+)?}", lambda m: str(SAMPLE[m.group(1)]), path)
    t = time.monotonic()
    try:
        resp = c.get(url, follow_redirects=False)
        t = time.monotonic()
        resp = c.get(url, follow_redirects=False)
        st = resp.status_code
        err = resp.text[:160].replace("\n", " ") if st >= 500 else ""
    except Exception as e:
        st, err = "EXC", repr(e)[:160]
    rows.append((url, st, round(time.monotonic() - t, 2), err))
bad = [x for x in rows if x[1] == "EXC" or (isinstance(x[1], int) and x[1] >= 500)]
slow = sorted([x for x in rows if x[2] >= 1.0], key=lambda x: -x[2])
import collections
print("못 채운 인자", collections.Counter(p for _, r in walk(app.routes) for p in re.findall(r"{(\w+)", getattr(r, "path", "")) if p not in SAMPLE).most_common(25))
print(f"주소 {len(rows)}개 · 오류 {len(bad)} · 1초 넘음 {len(slow)}  ids={ids}")
for x in bad: print("ERR", x)
for x in slow[:30]: print("SLOW", x[:3])
