"""공동 편집 부하 시험 — 한 문서에 N 명(창)이 붙어 각자 칸을 고칠 때 지연·실패율을 잰다(tmp 데이터, 실제 uvicorn).

각 창: 알림 흐름(SSE)을 열고, 제 칸 임대를 잡고, 입력 간격(throttle_ms, 기본 300ms)마다 칸 글을 보낸다(op), 5초마다 임대를 늘린다(touch).
잰 것: op 응답 시간(p50·p95·최대), 실패 수, 다른 창이 그 변경을 알림으로 받기까지 걸린 시간(전달 지연), 서버가 쓴 알림 수.

    PYTHONPATH=src python scripts/coedit_load.py --clients 4 8 12 16 --seconds 20
운영 서버에는 붙지 않는다 — 임시 폴더에 앱을 새로 띄운다(SQLite). 운영은 PostgreSQL 이라 쓰기 잠금이 더 가볍다(이 수치가 상한 쪽).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _MP:
    """pytest 의 monkeypatch 대신 — 테스트 도우미(_setup)가 setattr 만 쓴다."""

    def setattr(self, obj, name, value):
        setattr(obj, name, value)

    def setenv(self, k, v):
        os.environ[k] = v

    def delenv(self, k, raising=False):
        os.environ.pop(k, None)


def serve(n: int) -> None:
    """서버 쪽(따로 띄운 프로세스) — 임시 앱·계정 n 개·시험 문서를 만들고 {port, fid, fields} 한 줄을 찍은 뒤 uvicorn 으로 돈다."""
    import uvicorn
    from fastapi.testclient import TestClient
    from tests.test_accounts import _login
    from tests.test_webhwp import _setup, _staff, _up
    from zzaimy.app import webhwp_templates as wt
    from zzaimy.app import webhwp_write as ww
    from zzaimy.app import webhwp

    tmp = Path(tempfile.mkdtemp(prefix="coedit-load-"))
    app = _setup(tmp, _MP())
    for i in range(n):
        _staff(app, f"u{i:02d}", "기획처")
    owner = TestClient(app)
    assert _login(owner, "kim", "pass-12345678")
    fid = _up(owner, app, "기획처", "부하.hwpx", wt.build(wt.GRANT["budget_change"]))
    fields = list(ww.field_values(Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()))
    port = _free_port()
    from zzaimy.app import webhwp_coedit as ce
    calls = {"events_since": 0}
    orig = ce.events_since

    def counted(*a, **k):
        calls["events_since"] += 1
        return orig(*a, **k)
    ce.events_since = counted                       # 알림 읽기(DB 조회) 횟수 — 창이 늘 때 조회가 얼마나 느는지
    out = os.environ.get("COEDIT_LOAD_COUNT")
    if out:
        import signal

        def bye(*_):
            Path(out).write_text(json.dumps(calls))
            os._exit(0)
        signal.signal(signal.SIGTERM, bye)
    print(json.dumps({"port": port, "fid": fid, "fields": fields}, ensure_ascii=False), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def run(n: int, seconds: float, throttle_ms: float = 300.0) -> dict:
    import subprocess

    import httpx

    count_file = Path(tempfile.mkdtemp(prefix="coedit-count-")) / "count.json"
    proc = subprocess.Popen([sys.executable, __file__, "--serve", str(n)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}", "COEDIT_LOAD_COUNT": str(count_file)})
    info = None
    for line in proc.stdout:
        if line.startswith("{"):
            info = json.loads(line)
            break
    if not info:
        proc.kill()
        raise SystemExit("서버를 띄우지 못했다")
    url, fid, fields = f"http://127.0.0.1:{info['port']}", info["fid"], info["fields"]
    for _ in range(200):
        try:
            httpx.get(url + "/login", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    users = [f"u{i:02d}" for i in range(n)]
    cookies = {}
    for u in users:
        r = httpx.post(url + "/login", data={"username": u, "pw": "pass-12345678"}, follow_redirects=False)
        cookies[u] = r.cookies.get("zz_session")
    stop = threading.Event()
    lat: list[float] = []
    fails: list[str] = []
    deliver: list[float] = []
    lock = threading.Lock()
    events = {"n": 0}

    def listen(u: str):
        try:
            with httpx.Client(cookies={"zz_session": cookies[u]}, timeout=None) as c:
                with c.stream("GET", f"{url}/hwp/{fid}/events") as r:
                    if r.status_code != 200:
                        with lock:
                            fails.append(f"sse {r.status_code}")
                        return
                    kind = None
                    for line in r.iter_lines():
                        if stop.is_set():
                            return
                        if line.startswith("event: "):
                            kind = line[7:]
                        elif line.startswith("data: ") and kind == "field-updated":
                            d = json.loads(line[6:])
                            with lock:
                                events["n"] += 1
                            txt = str(d.get("text") or "")
                            if d.get("by") != u and "@" in txt:
                                try:
                                    sent = float(txt.rsplit("@", 1)[1])
                                except ValueError:
                                    continue
                                with lock:
                                    deliver.append(time.time() - sent)
        except Exception as e:  # 흐름이 끊기면 실패로 센다(멈출 때 끊긴 것은 빼고)
            if not stop.is_set():
                with lock:
                    fails.append(f"sse {type(e).__name__}")

    def writer(i: int, u: str):
        field = fields[i % len(fields)]
        client = f"{u}-w"
        seq = 0
        base = 0
        with httpx.Client(cookies={"zz_session": cookies[u]}, timeout=30) as c:
            r = c.post(f"{url}/hwp/{fid}/lease", json={"field": field, "action": "acquire", "client": client})
            if not r.json().get("ok"):
                with lock:
                    fails.append(f"lease {field}")
            last_touch = time.time()
            while not stop.is_set():
                seq += 1
                text = f"{u} 입력 {seq}@{time.time():.6f}"
                t0 = time.time()
                try:
                    r = c.post(f"{url}/hwp/{fid}/ops", json={"client": client, "ops": [{"seq": seq, "field": field, "base": base, "text": text}]})
                    dt = time.time() - t0
                    res = r.json()["results"][0] if r.status_code == 200 else {}
                    with lock:
                        lat.append(dt)
                        if not res.get("ok"):
                            fails.append(f"op {r.status_code} {res.get('reason') or res.get('error') or ''}"[:80])
                    base = int(res.get("version") or base)
                except Exception as e:
                    with lock:
                        fails.append(f"op {type(e).__name__}")
                if time.time() - last_touch > 5:
                    c.post(f"{url}/hwp/{fid}/lease", json={"action": "touch", "client": client, "fields": [field]})
                    last_touch = time.time()
                time.sleep(throttle_ms / 1000.0)

    ls = [threading.Thread(target=listen, args=(u,), daemon=True) for u in users]
    for t in ls:
        t.start()
    time.sleep(1.0)
    ws = [threading.Thread(target=writer, args=(i, u), daemon=True) for i, u in enumerate(users)]
    for t in ws:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in ws:
        t.join(10)
    proc.terminate()
    try:
        proc.wait(10)
    except Exception:
        proc.kill()
    reads = json.loads(count_file.read_text())["events_since"] if count_file.exists() else -1

    def pct(xs, p):
        if not xs:
            return 0.0
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * p))]

    return {"clients": n, "ops": len(lat), "ops_per_s": round(len(lat) / seconds, 1),
            "op_p50_ms": round(pct(lat, 0.5) * 1000), "op_p95_ms": round(pct(lat, 0.95) * 1000), "op_max_ms": round(max(lat or [0]) * 1000),
            "deliver_p50_ms": round(pct(deliver, 0.5) * 1000), "deliver_p95_ms": round(pct(deliver, 0.95) * 1000),
            "fails": len(fails), "fail_kinds": sorted(set(fails))[:6], "events_seen": events["n"],
            "event_reads": reads, "reads_per_op": round(reads / max(1, len(lat)), 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, nargs="+", default=[4, 8, 12, 16])
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--throttle-ms", type=float, default=300.0)
    ap.add_argument("--serve", type=int, default=0)
    a = ap.parse_args()
    if a.serve:
        serve(a.serve)
        return
    for n in a.clients:
        print(json.dumps(run(n, a.seconds, a.throttle_ms), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
