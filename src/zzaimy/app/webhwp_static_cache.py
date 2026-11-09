"""기안기 정적 파일(편집기 wasm·스크립트·그림) 캐시 — 여럿이 동시에 처음 열어도 기안기 서버·압축을 한 번만(11-10).

운영 실측(11-10): 배포 직후 9명이 같은 문서를 동시에 열자 모두 85초 넘게 「불러오는 중」. 혼자 다시 열면 3초.
원인은 문서가 아니라 편집기 정적 파일이었다 —
  · 프록시가 정적 파일을 요청마다 기안기 서버에서 통째로(wasm 13MB 등) 받아 왔고,
  · gzip 압축을 이벤트 루프 안에서 바로 해(13MB 를 압축하는 동안 서버 전체가 멈춤),
  · 압축본 캐시는 메모리에만 있어 재시작마다 비었고, 같은 파일을 동시에 받은 사람마다 따로 압축했다.
여기서는 정적 파일 응답을 (경로·쿼리)마다 한 번만 받아 메모리와 디스크에 두고, 같은 열쇠의 동시 요청은 한 번에 합치며(single-flight),
압축은 작업 스레드에서 한다. 기안기 판(version.txt)이 바뀌면 옛 캐시는 쓰지 않는다.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import threading
import time
from pathlib import Path

_MEM_MAX = int(os.environ.get("ZZAIMY_WEBHWP_STATIC_CACHE_MB", "256")) * 1024 * 1024   # 메모리 상한(넘으면 오래된 것부터 뺌)
_DISK_MAX = int(os.environ.get("ZZAIMY_WEBHWP_STATIC_DISK_MB", "512")) * 1024 * 1024   # 디스크 상한(넘으면 오래된 것부터 지움)
_ITEM_MAX = int(os.environ.get("ZZAIMY_WEBHWP_STATIC_ITEM_MB", "32")) * 1024 * 1024    # 항목 하나 상한(편집기 wasm 13MB) — 넘으면 담지 않는다
_KEYS_MAX = 600                                                 # 열쇠 수 상한 — 넘으면 새 열쇠는 담지 않는다(쿼리로 열쇠를 무한히 늘리지 못하게)
_WAIT_MAX = 64                                                  # 한 열쇠를 기다리는 요청 수 상한 — 넘으면 캐시 없이 평소대로
_KEEP_HEADERS = ("content-type", "last-modified", "etag")       # 캐시에 담는 응답 머리(쿠키·세션은 담지 않는다)


def cacheable_response(headers: dict) -> bool:
    """여럿이 나눠 써도 되는 응답인가 — 세션 쿠키를 주거나, 사람마다 다르다고(private·no-store·Vary: Cookie/Authorization) 밝힌 응답은 아니다."""
    h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    if "set-cookie" in h:
        return False
    if any(x in h.get("cache-control", "") for x in ("private", "no-store")):
        return False
    vary = h.get("vary", "")
    return not any(x in vary for x in ("cookie", "authorization", "*"))


class Entry:
    __slots__ = ("body", "gz", "headers", "version", "at", "size")

    def __init__(self, body: bytes, headers: dict, version: str, gz: bytes | None = None):
        self.body, self.headers, self.version, self.gz = body, headers, version, gz
        self.at = time.time()
        self.size = len(body) + len(gz or b"")


class StaticCache:
    def __init__(self) -> None:
        self._mem: dict[str, Entry] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._guard = threading.Lock()
        self._disk: Path | None = None
        self._waiting: dict[str, int] = {}
        self.stats = {"upstream": 0, "mem": 0, "disk": 0, "waited": 0, "bypass": 0}

    # ---- 열쇠·자리 ----
    @staticmethod
    def key(raw: str, query: str) -> str:
        return raw + ("?" + query if query else "")

    def set_dir(self, base: Path) -> None:
        d = Path(base) / "cache" / "webhwp_static"
        try:
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)                               # 앱 계정만 — 파일 이름은 열쇠의 해시(쿼리 원문을 드러내지 않는다)
            self._disk = d
        except OSError:
            self._disk = None

    def _file(self, key: str) -> Path | None:
        if self._disk is None:
            return None
        return self._disk / hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _lock(self, key: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        k = (key, id(loop))
        with self._guard:
            lk = self._locks.get(k)
            if lk is None:
                lk = self._locks[k] = asyncio.Lock()
            return lk

    # ---- 메모리·디스크 ----
    def _remember(self, key: str, e: Entry) -> None:
        with self._guard:
            self._mem[key] = e
            total = sum(x.size for x in self._mem.values())
            if total > _MEM_MAX:
                for k, _v in sorted(self._mem.items(), key=lambda kv: kv[1].at):
                    if total <= _MEM_MAX * 0.8 or k == key:
                        continue
                    total -= self._mem.pop(k).size

    def _load_disk(self, key: str, version: str) -> Entry | None:
        f = self._file(key)
        if f is None:
            return None
        meta = f.with_suffix(".json")
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            if m.get("key") != key or (version and m.get("version") != version):
                return None
            body = f.with_suffix(".bin").read_bytes()
            if hashlib.sha256(body).hexdigest() != m.get("sha256"):
                return None                                  # 반쯤 쓰인 파일 — 버린다
            gzp = f.with_suffix(".gz")
            gz = gzp.read_bytes() if gzp.exists() else None
            return Entry(body, dict(m.get("headers") or {}), m.get("version") or "", gz)
        except (OSError, ValueError):
            return None

    def _save_disk(self, key: str, e: Entry) -> None:
        f = self._file(key)
        if f is None:
            return
        try:
            for suf, data in ((".bin", e.body), (".gz", e.gz)):
                if data is None:
                    continue
                tmp = f.with_suffix(suf + ".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, f.with_suffix(suf))
            tmp = f.with_suffix(".json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "version": e.version, "headers": e.headers,
                                     "sha256": hashlib.sha256(e.body).hexdigest(), "size": len(e.body)}, ensure_ascii=False))
            os.replace(tmp, f.with_suffix(".json"))
            self._trim_disk()
        except OSError:
            pass

    def _trim_disk(self) -> None:
        """디스크 상한을 넘으면 오래된 항목부터 지운다."""
        if self._disk is None:
            return
        items = []
        for m in self._disk.glob("*.json"):
            parts = [m.with_suffix(x) for x in (".json", ".bin", ".gz")]
            size = sum(p.stat().st_size for p in parts if p.exists())
            items.append((m.stat().st_mtime, size, parts))
        total = sum(i[1] for i in items)
        for _t, size, parts in sorted(items):
            if total <= _DISK_MAX:
                break
            for p_ in parts:
                try:
                    p_.unlink()
                except OSError:
                    pass
            total -= size

    # ---- 꺼내기 ----
    async def get(self, key: str, version: str, fetch, want_gzip) -> Entry | None:
        """캐시에서 꺼내거나 한 번만 받아 온다. fetch() 는 (status, headers, body) 를 돌려주는 코루틴.
        200 이 아니면 None(캐시하지 않음 — 부른 쪽이 평소대로 넘긴다). want_gzip(body)→bool 이면 압축본도 만든다."""
        e = self._mem.get(key)
        if e is not None and (not version or e.version == version):
            self.stats["mem"] += 1
            return e
        with self._guard:
            if key not in self._mem and len(self._mem) >= _KEYS_MAX:
                self.stats["bypass"] += 1
                return None                                  # 열쇠가 너무 많다 — 담지 않고 평소대로
            if self._waiting.get(key, 0) >= _WAIT_MAX:
                self.stats["bypass"] += 1
                return None
            self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            return await self._get_locked(key, version, fetch, want_gzip)
        finally:
            with self._guard:
                n = self._waiting.get(key, 1) - 1
                if n <= 0:
                    self._waiting.pop(key, None)
                else:
                    self._waiting[key] = n

    async def _get_locked(self, key: str, version: str, fetch, want_gzip) -> Entry | None:
        lk = self._lock(key)
        if lk.locked():
            self.stats["waited"] += 1
        async with lk:                                       # 같은 열쇠의 동시 요청은 첫 요청 결과를 기다린다
            e = self._mem.get(key)
            if e is not None and (not version or e.version == version):
                self.stats["mem"] += 1
                return e
            from starlette.concurrency import run_in_threadpool
            e = await run_in_threadpool(self._load_disk, key, version)
            if e is not None:
                self.stats["disk"] += 1
                self._remember(key, e)
                return e
            status, headers, body = await fetch()
            self.stats["upstream"] += 1
            if status != 200 or not cacheable_response(headers) or len(body) > _ITEM_MAX:
                return None                                  # 사람마다 다를 수 있는 응답·너무 큰 응답은 담지 않는다
            keep = {k: v for k, v in headers.items() if k.lower() in _KEEP_HEADERS}
            gz = None
            if want_gzip(body):
                gz = await run_in_threadpool(gzip.compress, body, 6)   # 큰 wasm 압축이 이벤트 루프를 막지 않게
            e = Entry(body, keep, version, gz)
            self._remember(key, e)
            await run_in_threadpool(self._save_disk, key, e)
            return e

    def largest(self, n: int = 40) -> list[str]:
        """미리 받아 두기 목록 — 크기가 큰 정적 파일 열쇠부터(브라우저가 남는 시간에 받아 캐시에 둔다)."""
        with self._guard:
            sizes = {k: len(e.body) for k, e in self._mem.items()}
        if self._disk is not None:                           # 재시작 뒤 메모리가 비었어도 디스크에 둔 목록으로
            for m in self._disk.glob("*.json"):
                try:
                    j = json.loads(m.read_text(encoding="utf-8"))
                    sizes.setdefault(str(j.get("key") or ""), int(j.get("size") or 0))
                except (OSError, ValueError):
                    continue
        sizes.pop("", None)
        return [k for k, _s in sorted(sizes.items(), key=lambda kv: -kv[1])[:n]]


CACHE = StaticCache()
