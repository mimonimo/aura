"""화면 글(HTML·JSON 등) 압축 — 앞단 nginx 가 프록시 응답을 압축하지 않아(gzip_proxied 꺼짐) 화면 한 번에 200~430KB 를 그대로 보냈다(11-11).

인증서가 자체 서명이라 브라우저가 캐시도 하지 않으므로 매번 그만큼 받는다 — 앱에서 직접 줄인다.
고르는 것만 압축한다: 글 갈래(text/*·json·js·svg), 한 번에 끝나는 응답(흘려 보내는 SSE·내려받기는 그대로), 이미 압축된 것 제외,
작은 응답 제외. 한글 파일·그림·편집기 파일처럼 이미 압축된 것은 손대지 않는다."""
from __future__ import annotations

import gzip

_TYPES = ("text/html", "text/plain", "text/css", "application/json", "application/javascript", "text/javascript", "image/svg+xml")
MIN_SIZE = 1400


def _accepts_gzip(scope) -> bool:
    for k, v in scope.get("headers") or []:
        if k == b"accept-encoding":
            return b"gzip" in v.lower()
    return False


class GzipText:
    def __init__(self, app, level: int = 5):
        self.app = app
        self.level = level

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _accepts_gzip(scope):
            await self.app(scope, receive, send)
            return
        start: dict | None = None
        passthrough = False

        async def wrapped(message):
            nonlocal start, passthrough
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start":
                hdrs = {k.lower(): v for k, v in message.get("headers") or []}
                ctype = hdrs.get(b"content-type", b"").decode("latin-1").lower()
                if b"content-encoding" in hdrs or not ctype.startswith(_TYPES):
                    passthrough = True
                    await send(message)
                    return
                start = message                               # 몸통 첫 조각을 보고 정한다
                return
            if message["type"] == "http.response.body" and start is not None:
                body = message.get("body", b"")
                if message.get("more_body") or len(body) < MIN_SIZE:
                    passthrough = True                         # 흘려 보내는 응답·작은 응답은 그대로
                    await send(start)
                    start = None
                    await send(message)
                    return
                z = gzip.compress(body, compresslevel=self.level)
                headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() not in (b"content-length", b"vary")]
                vary = [v for k, v in start.get("headers") or [] if k.lower() == b"vary"]
                headers += [(b"content-encoding", b"gzip"), (b"content-length", str(len(z)).encode()),
                            (b"vary", (vary[0] + b", Accept-Encoding") if vary else b"Accept-Encoding")]
                await send({**start, "headers": headers})
                start = None
                await send({"type": "http.response.body", "body": z})
                return
            await send(message)

        await self.app(scope, receive, wrapped)
