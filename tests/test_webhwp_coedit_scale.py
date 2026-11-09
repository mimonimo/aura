"""공동 편집 규모(11-10) — 한 문서에 창이 여럿 붙어도 알림을 빠짐없이 받고, 알림 읽기(DB)는 창 수만큼 늘지 않는다."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _setup, _staff, _up
from tests.test_webhwp_coedit import _free_port
from zzaimy.app import webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


def test_shared_lookup_follows_generation_and_db(tmp_path, monkeypatch):
    """같이 읽은 알림은 같은 세대(깨움 전)·같은 DB 에서만 나눠 쓴다 — 깨움 뒤엔 다시 읽게 None, 다른 앱(DB)은 섞지 않는다."""
    app = _setup(tmp_path, monkeypatch)
    db = app.state.db
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    fid = _up(kim, app, "기획처", "나눠읽기.hwpx", wt.build(wt.GRANT["budget_change"]))   # 장부에 있는 문서(없는 번호의 알림은 정리가 지운다)
    ce.emit(db, fid, "presence", {"user": "kim", "client": "a", "field": ""})
    gen = ce.HUB.generation(fid)
    evs = ce.events_since(db, fid, 0, 20, full=False)
    assert evs
    got = ce.shared_put(db, fid, gen, 0, evs)
    assert [e["id"] for e in got] == [e["id"] for e in evs]
    hit = ce.shared_lookup(db, fid, 0)
    assert hit is not None and [e["id"] for e in hit] == [e["id"] for e in evs]
    hit[0]["data"]["by_name"] = "바뀜"                                   # 창마다 고쳐 써도 남의 창에 번지지 않는다(복사본)
    assert "by_name" not in ce.shared_lookup(db, fid, 0)[0]["data"]

    class Other:
        path = str(tmp_path / "다른.db")
    assert ce.shared_lookup(Other(), fid, 0) is None                    # 다른 DB 는 따로
    ce.emit(db, fid, "presence", {"user": "park", "client": "b", "field": ""})   # 깨움 → 세대가 바뀐다
    assert ce.shared_lookup(db, fid, 0) is None
    assert ce.shared_lookup(db, fid, 0, max_age=0.0) is None


def test_many_windows_all_receive_every_change_with_few_reads(tmp_path, monkeypatch):
    """실제 서버에 창 8개가 알림 흐름을 열고 4명이 칸을 고칠 때 — 모든 창이 모든 변경을 받고, 알림 읽기는 변경 수의 2배를 넘지 않는다
    (예전에는 변경 하나에 창 수만큼 읽어 16창에서 처리량이 막혔다)."""
    import uvicorn
    app = _setup(tmp_path, monkeypatch)
    users = [f"u{i}" for i in range(8)]
    for u in users:
        _staff(app, u, "기획처")
    owner = TestClient(app)
    assert _login(owner, "kim", "pass-12345678")
    fid = _up(owner, app, "기획처", "규모.hwpx", wt.build(wt.GRANT["budget_change"]))
    fields = list(ww.field_values(Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()))
    jar = {}
    for u in users:
        c = TestClient(app)
        assert _login(c, u, "pass-12345678")
        jar[u] = c.cookies.get("zz_session")
    reads = {"n": 0}
    orig = ce.events_since

    def counted(*a, **k):
        reads["n"] += 1
        return orig(*a, **k)
    monkeypatch.setattr(ce, "events_since", counted)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    got = {u: set() for u in users}
    ready = threading.Barrier(len(users) + 1, timeout=10)
    stop = threading.Event()

    def listen(u):
        with httpx.Client(cookies={"zz_session": jar[u]}, timeout=None) as c:
            with c.stream("GET", f"{url}/hwp/{fid}/events") as r:
                ready.wait()
                kind = None
                for line in r.iter_lines():
                    if stop.is_set():
                        return
                    if line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: ") and kind == "field-updated":
                        d = json.loads(line[6:])
                        got[u].add((d["field"], d.get("version")))
    ts = [threading.Thread(target=listen, args=(u,), daemon=True) for u in users]
    try:
        for t in ts:
            t.start()
        ready.wait()
        time.sleep(0.3)
        reads["n"] = 0
        sent = set()
        writers = users[:4]
        for rnd in range(5):
            for i, u in enumerate(writers):
                f = fields[i]
                with httpx.Client(cookies={"zz_session": jar[u]}, timeout=10) as c:
                    if rnd == 0:
                        assert c.post(f"{url}/hwp/{fid}/lease", json={"field": f, "action": "acquire", "client": f"{u}-w"}).json()["ok"]
                    r = c.post(f"{url}/hwp/{fid}/ops", json={"client": f"{u}-w", "ops": [{"seq": rnd + 1, "field": f, "base": rnd, "text": f"{u} {rnd}"}]}).json()["results"][0]
                    assert r["ok"], r
                    sent.add((f, r["version"]))
        deadline = time.time() + 5
        while time.time() < deadline and not all(sent <= got[u] for u in users):
            time.sleep(0.1)
        missing = {u: len(sent - got[u]) for u in users if sent - got[u]}
        assert not missing, missing                                   # 모든 창이 모든 변경을 받는다
        assert reads["n"] <= 2 * len(sent) + 8, reads["n"]           # 창 8개여도 변경당 읽기는 몇 번 안 된다(예전엔 변경×창)
    finally:
        stop.set()
        server.should_exit = True
        th.join(5)
