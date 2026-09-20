"""검색이 지금 어디서 도는가 — 질의 임베딩·리랭킹의 실제 위치를 화면에 알린다.

왜 따로 두는가: 이 둘은 '연결'로 고르는 것이 아니라 서빙 서비스로 정해진다
(`ZZAIMY_EMBED_URL`·`ZZAIMY_RERANK_URL`, scripts/102·103). 화면에서 서버를 고르게 해 두면
고르는 대로 되지 않는 칸이 된다 — 그래서 고르는 칸이 아니라 지금 상태를 보여 준다.
서비스가 죽으면 VM 쪽으로 물러나므로, 지금 어디서 도는지가 성능·품질 판단에 필요하다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

_TTL = 120.0
_cache: dict[str, tuple[float, dict]] = {}

PARTS = (
    ("embed", "문장 임베딩", "ZZAIMY_EMBED_URL", "VM 안에서 계산 (CPU)"),
    ("rerank", "리랭킹", "ZZAIMY_RERANK_URL", "VM 안에서 계산 (CPU)"),
)


def _health(url: str) -> dict:
    """서비스의 /health — 주소에서 끝 경로를 떼고 물어본다."""
    base = url.rstrip("/")
    for tail in ("/embed", "/score"):
        if base.endswith(tail):
            base = base[: -len(tail)]
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3.0) as r:
            got = json.loads(r.read().decode("utf-8"))
        return {"ok": bool(got.get("ok")), "model": str(got.get("model") or ""),
                "detail": f"{got.get('max_length')}토큰" if got.get("max_length") else ""}
    except (urllib.error.URLError, OSError, ValueError, TypeError) as e:
        return {"ok": False, "model": "", "detail": type(e).__name__}


def status(ttl: float = _TTL) -> list[dict]:
    """[{key, label, where, model, ok, remote}] — 화면에 그대로 뿌릴 수 있는 형태."""
    out = []
    for key, label, env, local_label in PARTS:
        url = os.environ.get(env, "").strip()
        if not url:
            out.append({"key": key, "label": label, "where": local_label,
                        "model": "", "ok": True, "remote": False, "detail": ""})
            continue
        hit = _cache.get(key)
        got = hit[1] if hit and time.time() - hit[0] < ttl else None
        if got is None:
            got = _health(url)
            _cache[key] = (time.time(), got)
        host = url.split("//")[-1].split("/")[0]
        detail = got["detail"] if got["ok"] else "응답 없음 — VM 으로 물러납니다"
        if key == "rerank" and got["ok"]:
            # 하한은 모델마다 다시 재야 하는 값이다(scripts/105) — 화면에 지금 값을 적어
            # 모델만 바꾸고 하한을 안 고친 상태가 눈에 보이게 한다
            from zzaimy.app.rerank import RERANK_MIN

            floor = os.environ.get("ZZAIMY_RERANK_MIN", "").strip() or f"{RERANK_MIN}"
            detail = f"{detail} · 근거 하한 {floor}" if detail else f"근거 하한 {floor}"
        out.append({"key": key, "label": label, "where": f"서빙 장비 {host}",
                    "model": (got["model"] or "").rsplit("/", 1)[-1],
                    "ok": got["ok"], "remote": True, "detail": detail})
    return out


def clear_cache() -> None:
    _cache.clear()
