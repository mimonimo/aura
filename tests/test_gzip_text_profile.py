import asyncio
import gzip

from zzaimy.app.gzip_text import GzipText
from zzaimy.app import profile_sampler


def _run(app, headers):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(m):
        sent.append(m)

    asyncio.run(GzipText(app)({"type": "http", "headers": headers}, receive, send))
    return sent


def _resp(ctype, chunks, extra=()):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", ctype), (b"content-length", b"1")] + list(extra)})
        for i, c in enumerate(chunks):
            await send({"type": "http.response.body", "body": c, "more_body": i < len(chunks) - 1})
    return app


ACC = [(b"accept-encoding", b"gzip, br")]


def test_html_compressed():
    body = ("가나다" * 2000).encode()
    sent = _run(_resp(b"text/html; charset=utf-8", [body]), ACC)
    h = dict(sent[0]["headers"])
    assert h[b"content-encoding"] == b"gzip"
    assert gzip.decompress(sent[1]["body"]) == body
    assert int(h[b"content-length"]) == len(sent[1]["body"])


def test_skip_without_accept_small_stream_binary_encoded():
    big = b"x" * 5000
    for app, hdr in [
        (_resp(b"text/html", [big]), []),                                   # 받는 쪽이 압축 못 받음
        (_resp(b"text/html", [b"small"]), ACC),                             # 작음
        (_resp(b"text/event-stream", [big, big]), ACC),                     # 흘려 보냄
        (_resp(b"text/html", [big, big]), ACC),                             # 여러 조각
        (_resp(b"application/octet-stream", [big]), ACC),                   # 글 아님
        (_resp(b"application/wasm", [big], [(b"content-encoding", b"gzip")]), ACC),  # 이미 압축
    ]:
        sent = _run(app, hdr)
        assert all(k != b"content-encoding" or v != b"gzip" or b"wasm" in dict(sent[0]["headers"])[b"content-type"]
                   for k, v in sent[0]["headers"])
        assert b"".join(m.get("body", b"") for m in sent[1:]) in (big, big + big, b"small")


def test_sampler_reports_busy_thread():
    import threading
    stop = threading.Event()

    def spin_here():
        while not stop.is_set():
            sum(range(1000))

    t = threading.Thread(target=spin_here, daemon=True)
    t.start()
    try:
        out = profile_sampler.sample(0.6, only="spin_here")
    finally:
        stop.set()
        t.join()
    assert "spin_here" in out
    assert "표본" in out


def test_watch_file_trigger(tmp_path):
    import json
    import time as _t
    profile_sampler.start_watch(tmp_path, every=0.1)
    (tmp_path / ".profile_request").write_text(json.dumps({"seconds": 0.5}), encoding="utf-8")
    for _ in range(60):
        if (tmp_path / ".profile_out.txt").exists():
            break
        _t.sleep(0.1)
    assert "표본" in (tmp_path / ".profile_out.txt").read_text(encoding="utf-8")
    assert not (tmp_path / ".profile_request").exists()
