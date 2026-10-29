#!/usr/bin/env python3
"""화면별 시간·프로파일 — 주소 목록 파일의 화면을 열어 처음·두 번째 시간을 재고, 느리면 우리 코드 함수별 누적 시간을 보인다(10-28).

주소 목록은 한 줄에 하나. 두 번째 인자는 프로파일할 기준 초, --cold 면 처음 열기를 프로파일.
사용(VM, 운영 폴더에서 .env.local 과 함께):
  cd ~/zzaimy-capstone && set -a && . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/221_page_profile.py 주소목록.txt 1.0 [--cold]
"""
import cProfile, io, pstats, sys, threading, time
from pathlib import Path
_orig_start = threading.Thread.start
_SKIP = {"auto-reindex", "drive-share-sweep", "drive-share", "weekly-schedule", "org-reconcile", "requeue-stalled", "warm-nouns"}
def _start(self):                                   # 배경 작업만 끔(측정만) — 요청 처리 스레드는 그대로
    if self.name in _SKIP:
        return
    _orig_start(self)
threading.Thread.start = _start
from fastapi.testclient import TestClient
from zzaimy.app.main import create_app
from zzaimy.app.drafter import SliceDrafter
from zzaimy.app.pipeline import DocumentProcessor
app = create_app(db_path=Path("data/platform/platform.db"), inbox_dir=Path("data/platform/inbox"),
                 processor=DocumentProcessor(), drafter=SliceDrafter(), responder=None, password=None)
c = TestClient(app, raise_server_exceptions=False)
paths = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.startswith("#")]
prof_over = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
res = []
COLD = "--cold" in sys.argv
for p in paths:
    if COLD:
        pr = cProfile.Profile(); pr.enable()
    t = time.monotonic(); r = c.get(p, follow_redirects=False); a = time.monotonic() - t
    if COLD:
        pr.disable(); s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats("/src/zzaimy/", 16)
        print("\n".join("   " + l for l in s.getvalue().splitlines() if "/src/zzaimy/" in l)[:3000])
    t = time.monotonic(); r2 = c.get(p, follow_redirects=False); b = time.monotonic() - t
    res.append((p, r.status_code, round(a, 2), round(b, 2)))
    print(f"{r.status_code} {a:6.2f} {b:6.2f} {p}", flush=True)
    if r.status_code >= 500:
        print("   ERR", r.text[:300].replace("\n", " "))
    if b > prof_over:
        pr = cProfile.Profile(); pr.enable(); c.get(p, follow_redirects=False); pr.disable()
        s = io.StringIO(); st = pstats.Stats(pr, stream=s); st.sort_stats("cumulative").print_stats("/src/zzaimy/", 18)
        print("\n".join("   " + l for l in s.getvalue().splitlines() if "/src/zzaimy/" in l)[:3500])
