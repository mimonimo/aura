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
        return 200, {"content-type": "application/wasm", "etag": "W/1"}, body

    async def go():
        return await asyncio.gather(*[c.get(c.key("hwp.wasm", "v=1"), "13.82", fetch, lambda b: True) for _ in range(9)])

    res = _run(go())
    assert calls["n"] == 1                                    # 9명이 동시에 처음 열어도 한 번만
    assert all(r.body == body for r in res)
    assert gzip.decompress(res[0].gz) == body
    assert set(k.lower() for k in res[0].headers) <= {"content-type", "last-modified", "etag"}   # 담는 머리는 정해진 것만


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


# ---- 보안 검토(11-10): 사람마다 다른 응답·내부 정보·자원 상한 우회 ----

def test_only_editor_assets_with_version_query_are_shared():
    from zzaimy.app.webhwp_routes import _shared_static
    assert _shared_static("js/hwpctrlapp/hwpCtrlApp.js", "")
    assert _shared_static("wasm/eqeditwa.wasm", "13.82.0.208")
    assert _shared_static("commonFrame/skins/default/css/a.css", "v=13.82")
    for raw, q in [("serverProps", ""), ("hwpctrlmain.html", ""), ("api/doc.js", ""),          # API·문서·배포물 밖
                   ("js/app.js", "id=2397"), ("js/app.js", "user=kim"), ("js/app.js", "sid=abc"),   # 문서·사용자·세션 값이 쿼리에
                   ("js/app.js", "13.82&x=1"), ("../x.js", ""), ("upload/2397/file.png", "")]:
        assert not _shared_static(raw, q), (raw, q)


def test_private_or_cookie_responses_are_not_cached(tmp_path):
    from zzaimy.app.webhwp_static_cache import cacheable_response
    for h in [{"Set-Cookie": "JSESSIONID=1"}, {"Cache-Control": "private"}, {"cache-control": "no-store"},
              {"Vary": "Cookie"}, {"vary": "Accept-Encoding, Authorization"}, {"Vary": "*"}]:
        assert not cacheable_response(h), h
    assert cacheable_response({"Content-Type": "application/wasm", "Cache-Control": "no-cache", "Vary": "Accept-Encoding"})
    c = StaticCache(); c.set_dir(tmp_path)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return 200, {"Set-Cookie": "s=1"}, b"x"

    assert _run(c.get("js/a.js", "v", fetch, lambda b: False)) is None
    assert _run(c.get("js/a.js", "v", fetch, lambda b: False)) is None and calls["n"] == 2


def test_disk_cache_is_private_and_hashed(tmp_path):
    import os, stat
    c = StaticCache(); c.set_dir(tmp_path)

    async def fetch():
        return 200, {"content-type": "text/javascript"}, b"a" * 10000

    _run(c.get("js/secret-name.js?13.82", "v", fetch, lambda b: True))
    d = tmp_path / "cache" / "webhwp_static"
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    files = list(d.iterdir())
    assert files and all(stat.S_IMODE(os.stat(f).st_mode) == 0o600 for f in files)
    assert not any("secret" in f.name for f in files)          # 파일 이름은 해시


def test_caps_item_keys_disk_and_waiters(tmp_path, monkeypatch):
    from zzaimy.app import webhwp_static_cache as sc
    monkeypatch.setattr(sc, "_ITEM_MAX", 1000)
    monkeypatch.setattr(sc, "_KEYS_MAX", 3)
    monkeypatch.setattr(sc, "_DISK_MAX", 25_000)
    monkeypatch.setattr(sc, "_WAIT_MAX", 2)
    c = StaticCache(); c.set_dir(tmp_path)

    async def big():
        return 200, {}, b"b" * 5000

    assert _run(c.get("js/big.js", "v", big, lambda b: False)) is None      # 항목 하나 상한
    assert "js/big.js" not in c._mem

    async def small():
        return 200, {}, b"s" * 900

    for i in range(5):
        _run(c.get(f"js/k{i}.js", "v", small, lambda b: False))
    assert len(c._mem) == 3 and c.stats["bypass"] >= 2                      # 열쇠 수 상한 — 새 열쇠는 담지 않는다

    c2 = StaticCache(); c2.set_dir(tmp_path / "d2")

    async def mid():
        return 200, {}, b"m" * 900

    monkeypatch.setattr(sc, "_KEYS_MAX", 1000)
    for i in range(40):
        _run(c2.get(f"js/m{i}.js", "v", mid, lambda b: False))
    disk = sum(f.stat().st_size for f in (tmp_path / "d2" / "cache" / "webhwp_static").iterdir())
    assert disk <= 25_000                                                    # 디스크 상한 — 오래된 것부터 지움

    c3 = StaticCache(); c3.set_dir(tmp_path / "d3")
    calls = {"n": 0}

    async def slow():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return 200, {}, b"w" * 100

    async def go():
        return await asyncio.gather(*[c3.get("js/w.js", "v", slow, lambda b: False) for _ in range(6)])

    res = _run(go())
    assert sum(1 for r in res if r is None) >= 4                             # 기다리는 수 상한 — 넘친 요청은 캐시 없이 평소대로
