"""모델 호출 동시 개수 문 — 여러 담당자가 한꺼번에 요청해 서빙 장비(토르 2대)에 수십 건이 몰리면 호출마다 느려져
제한 시간(ZZAIMY_LLM_TIMEOUT)을 넘겨 실패가 번진다. 동시에 나가는 호출 수를 ZZAIMY_LLM_SLOTS(기본 16)로 두고,
넘는 호출은 차례를 기다린다. 기다리는 수(waiting)는 화면이 「모델 대기 중 · 앞에 N건」으로 보인다(11-10)."""
from __future__ import annotations

import contextlib
import os
import threading

_SLOTS = max(1, int(os.environ.get("ZZAIMY_LLM_SLOTS", "16") or 16))
_sem = threading.BoundedSemaphore(_SLOTS)
_lock = threading.Lock()
_state = {"waiting": 0, "running": 0}


def waiting() -> int:
    return _state["waiting"]


def running() -> int:
    return _state["running"]


@contextlib.contextmanager
def slot():
    """호출 하나가 문을 지나는 동안 — 빈자리가 없으면 기다린다(기다리는 수를 센다)."""
    with _lock:
        _state["waiting"] += 1
    try:
        _sem.acquire()
    finally:
        with _lock:
            _state["waiting"] -= 1
    with _lock:
        _state["running"] += 1
    try:
        yield
    finally:
        with _lock:
            _state["running"] -= 1
        _sem.release()
