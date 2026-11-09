"""편집기 첫 열기 부하 측정 — N명이 동시에 「차가운 브라우저」로 한글 편집기를 열 때 정적 파일을 다 받는 데 걸리는 시간(11-10).

쓰는 법(운영에서 읽기만 — 계정은 시험 계정):
  AURA_BASE=https://192.168.16.226 AURA_ACCTS=~/ops/test-accounts.txt python scripts/editor_open_load.py 1 4 9
계정 파일은 「아이디\n비밀번호」 줄 쌍. 사람마다 로그인 → /webhwp-prefetch 목록의 파일을 모두 받음(gzip) → 걸린 초·받은 MB.
목록이 비었으면(배포 직후) 한 사람이 편집기를 한 번 열어 캐시가 차게 한 뒤 다시 잰다.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("AURA_BASE", "https://192.168.16.226").rstrip("/")


def accounts() -> list[tuple[str, str]]:
    lines = [x.strip() for x in Path(os.path.expanduser(os.environ.get("AURA_ACCTS", "~/ops/test-accounts.txt"))).read_text().splitlines() if x.strip()]
    return list(zip(lines[0::2], lines[1::2]))


async def one(uid: str, pw: str) -> tuple[float, int, int]:
    async with httpx.AsyncClient(base_url=BASE, verify=False, timeout=300, follow_redirects=False) as c:
        await c.post("/login", data={"username": uid, "user": uid, "id": uid, "password": pw, "pw": pw})
        j = (await c.get("/webhwp-prefetch")).json()
        t0, got = time.time(), 0
        for u in j.get("urls", []):
            r = await c.get(u, headers={"accept-encoding": "gzip"})
            got += len(r.content)
        return time.time() - t0, got, len(j.get("urls", []))


async def main(ns: list[int]) -> None:
    acc = accounts()
    for n in ns:
        t = time.time()
        res = await asyncio.gather(*[one(*acc[i % len(acc)]) for i in range(n)])
        secs = sorted(r[0] for r in res)
        print(f"{n}명 동시: 가장 느린 {secs[-1]:.1f}초 · 중앙 {secs[len(secs)//2]:.1f}초 · 한 명 {res[0][1]/1e6:.1f}MB({res[0][2]}개) · 전체 {time.time()-t:.1f}초")


if __name__ == "__main__":
    asyncio.run(main([int(x) for x in sys.argv[1:]] or [1, 4, 9]))
