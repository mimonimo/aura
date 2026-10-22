"""평가 스크립트의 후보 모델 연결 — scripts/195 와 같은 방식(ZZAIMY_LLM_TRY_CONN·ZZAIMY_LLM_TRY_MODEL → llm_connections.trial_conn).

운영 용도 지정은 바꾸지 않고, 이 프로세스에서만 answer·review·기본 연결을 후보로 돌린다. 결과에는 모델 정보와 연결 이름·id 만 남기고
서버 주소는 남기지 않는다. 채점처럼 판마다 같아야 하는 호출은 fixed_client 로 후보 설정을 빼고 운영 연결을 쓴다.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

TRIAL_ENV = ("ZZAIMY_LLM_TRY_CONN", "ZZAIMY_LLM_TRY_MODEL")


def apply(conn: str = "", model: str = "") -> dict[str, str]:
    """후보 설정을 이 프로세스 환경에 얹는다(빈 값이면 운영 그대로). 돌려주는 것은 얹은 값."""
    env = {}
    if conn:
        env["ZZAIMY_LLM_TRY_CONN"] = conn
    if model:
        env["ZZAIMY_LLM_TRY_MODEL"] = model
    for k in TRIAL_ENV:
        os.environ.pop(k, None)
    os.environ.update(env)
    return env


@contextmanager
def without_trial():
    saved = {k: os.environ.pop(k, None) for k in TRIAL_ENV}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def fixed_client(role: str = "review"):
    """후보 설정을 뺀 운영 연결 — 판끼리 같은 채점자."""
    from zzaimy.generate.client import VllmClient
    with without_trial():
        return VllmClient(role=role)


def _no_address(d: dict) -> dict:
    return {k: v for k, v in (d or {}).items() if not re.search(r"url|addr|host|endpoint|key", str(k), re.I)}


def snapshot(roles: tuple[str, ...] = ("answer", "review")) -> dict:
    """지금(후보 설정을 얹은 상태) 용도별 모델 정보 — 모델 이름·바탕·어댑터, 연결 이름·id·종류. 주소·키는 넣지 않는다."""
    from zzaimy.generate import llm_connections, model_info
    from zzaimy.generate.client import VllmClient
    out: dict = {"trial": {k: os.environ.get(k, "") for k in TRIAL_ENV if os.environ.get(k)}}
    for role in roles:
        try:
            c = VllmClient(role=role)
            conn = llm_connections.get(c.connection_id) if c.connection_id else None
            out[role] = {"llm": _no_address(model_info.stamp(c)), "label": model_info.label(model_info.stamp(c)),
                         "conn": (conn or {}).get("name", "환경변수"), "conn_id": c.connection_id, "kind": c.kind}
        except Exception as e:
            out[role] = {"error": type(e).__name__}
    return out
