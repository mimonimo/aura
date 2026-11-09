"""기안기 정적 파일 캐시 — 동시 요청은 기안기 서버를 한 번만, 재시작 뒤 디스크에서, 판이 바뀌면 다시(11-10 9명 동시 열기 85초)."""
import asyncio
import gzip

from zzaimy.app.webhwp_static_cache import StaticCache


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_concurrent_misses_fetch_upstream_once(tmp_path):
    c = StaticCache()
    c.set_dir(tmp_path)
    calls = {"n": 0}
    body = b"x" * 200_000

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return 200, {"content-type": "application/wasm", "set-cookie": "s=1"}, body

    async def go():
        return await asyncio.gather(*[c.get(c.key("hwp.wasm", "v=1"), "13.82", fetch, lambda b: True) for _ in range(9)])

    res = _run(go())
    assert calls["n"] == 1                                    # 9명이 동시에 처음 열어도 한 번만
    assert all(r.body == body for r in res)
    assert gzip.decompress(res[0].gz) == body
    assert "set-cookie" not in {k.lower() for k in res[0].headers}   # 세션 쿠키는 캐시에 담지 않는다


def test_disk_survives_restart_and_version_change_refetches(tmp_path):
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return 200, {"content-type": "text/javascript"}, b"console.log(1);" * 1000

    a = StaticCache(); a.set_dir(tmp_path)
    _run(a.get("app.js", "v1", fetch, lambda b: True))
    b = StaticCache(); b.set_dir(tmp_path)                    # 재시작 — 메모리는 비었다
    e = _run(b.get("app.js", "v1", fetch, lambda b: True))
    assert calls["n"] == 1 and e.gz is not None and b.stats["disk"] == 1
    _run(b.get("app.js", "v2", fetch, lambda b: True))        # 기안기 판이 바뀌면 다시 받는다
    assert calls["n"] == 2
    assert b.largest(5) == ["app.js"]


def test_non_200_is_not_cached(tmp_path):
    c = StaticCache(); c.set_dir(tmp_path)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return 404, {}, b"no"

    assert _run(c.get("missing.js", "v1", fetch, lambda b: True)) is None
    assert _run(c.get("missing.js", "v1", fetch, lambda b: True)) is None
    assert calls["n"] == 2


def test_proxy_serves_static_from_cache_and_prefetch_list(tmp_path, monkeypatch):
    """프록시 경로: 같은 정적 파일 두 번째부터 기안기 서버에 묻지 않고, 미리 받기 목록에 나온다."""
    import httpx
    from zzaimy.app import webhwp, webhwp_static_cache as sc

    monkeypatch.setattr(sc, "CACHE", StaticCache())
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": True, "version": "13.82", "error": ""})
    hits = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None, headers=None):
            hits["n"] += 1
            return httpx.Response(200, headers={"content-type": "application/wasm"}, content=b"w" * 50_000)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zzaimy.app import webhwp_routes as wr

    app = FastAPI()
    app.state.data_dir = str(tmp_path)

    @app.middleware("http")
    async def _role(request, call_next):
        request.state.role = "staff"
        return await call_next(request)

    app.include_router(wr.router)
    cl = TestClient(app)
    r1 = cl.get("/webhwpctrl/js/hwp.wasm?13.82", headers={"accept-encoding": "gzip"})
    r2 = cl.get("/webhwpctrl/js/hwp.wasm?13.82", headers={"accept-encoding": "gzip"})
    assert r1.status_code == r2.status_code == 200 and r2.content == b"w" * 50_000
    assert hits["n"] == 1
    assert "max-age" in r2.headers.get("cache-control", "")
    j = cl.get("/webhwp-prefetch").json()
    assert j["version"] == "13.82" and j["urls"] == ["/webhwpctrl/js/hwp.wasm?13.82"]
