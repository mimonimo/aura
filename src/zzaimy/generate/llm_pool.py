"""같은 모델을 내는 내부 서버 여럿에 호출을 고르게 나눈다(11-10 동시 4건 실측: 용도 지정이 답·걸음을 모두 토르 02 한 대로 보내고
토르 03 은 판독·검토에만 쓰였다 — 몰리면 한 대에서 생성 속도가 나뉜다).

규칙(좁게): 같은 종류(vllm)·내부 연결이고, 내는 모델 이름이 같고, 키가 같은(교내 서버는 보통 키 없음) 연결끼리만 「쌍둥이」로 본다.
그 가운데 지금 처리 중인 호출이 가장 적은 곳을 고르고, 같으면 돌아가며 고른다. 외부 연결·키가 다른 서버로는 절대 넘기지 않는다
(키가 다른 서버로 새지 않게). ZZAIMY_LLM_POOL=0 이면 끈다(용도 지정 그대로)."""
from __future__ import annotations

import contextlib
import itertools
import os
import threading

_lock = threading.Lock()
_inflight: dict[str, int] = {}
_rr = itertools.count()


def enabled() -> bool:
    return os.environ.get("ZZAIMY_LLM_POOL", "1").strip().lower() not in ("0", "false", "no", "off")


def _norm(url: str) -> str:
    return (url or "").rstrip("/")


def twins(cfg: dict, connections: list[dict], resolve_key) -> list[str]:
    """cfg(지금 고른 연결)과 같은 모델을 내는 내부 서버 주소들 — cfg 자신 포함, 차례는 설정 순서."""
    base = _norm(cfg.get("base_url", ""))
    if cfg.get("external") or cfg.get("kind", "vllm") != "vllm" or not base:
        return [base] if base else []
    model = (cfg.get("model") or cfg.get("vision_model") or "").strip()
    if not model:
        return [base]
    key = cfg.get("api_key") or ""
    out = []
    for c in connections:
        if c.get("kind", "vllm") != "vllm" or c.get("external"):
            continue
        served = {(c.get("model") or "").strip(), (c.get("vision_model") or "").strip()} - {""}
        if model not in served:
            continue
        try:
            if (resolve_key(c) or "dummy") != (key or "dummy"):
                continue
        except Exception:
            continue
        u = _norm(c.get("base_url", ""))
        if u and u not in out:
            out.append(u)
    if base not in out:
        out.insert(0, base)
    return out


def pick(urls: list[str]) -> str:
    """처리 중 호출이 가장 적은 주소 — 같으면 돌아가며."""
    if not urls:
        return ""
    if len(urls) == 1:
        return urls[0]
    with _lock:
        low = min(_inflight.get(u, 0) for u in urls)
        cands = [u for u in urls if _inflight.get(u, 0) == low]
        return cands[next(_rr) % len(cands)]


@contextlib.contextmanager
def track(url: str):
    """이 주소로 나간 호출 하나를 센다 — pick 이 덜 바쁜 쪽을 고르는 데 쓴다."""
    u = _norm(url)
    with _lock:
        _inflight[u] = _inflight.get(u, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _inflight[u] = max(0, _inflight.get(u, 1) - 1)


def inflight() -> dict[str, int]:
    with _lock:
        return dict(_inflight)
