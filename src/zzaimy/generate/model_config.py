"""LLM 서버 주소·모델 선택 — 화면 설정(DB) > 환경변수 > 기본값.

화면에서 고른 값은 set_override() 로 프로세스 전체(채팅·검토·초안·파이프라인)가 같이 쓴다.
서버가 여러 모델을 올려 두면 probe() 로 목록을 받아 고른다.
값이 없으면 서버의 첫 모델을 쓴다(기존 동작).
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "http://localhost:8000/v1"
_override: dict[str, str] = {}


def set_override(base_url: str = "", model: str = "") -> None:
    _override.clear()
    _status_cache.update(at=0.0, key="", result=None)
    if base_url.strip():
        _override["base_url"] = base_url.strip()
    if model.strip():
        _override["model"] = model.strip()


def current(role: str = "") -> dict:
    """지금 쓸 서버·모델. role 을 주면 그 용도로 지정한 것(없으면 문서 작업 기본)을 쓴다."""
    from zzaimy.generate import llm_connections

    conn = llm_connections.trial_conn(role)          # 후보 모델 점검(그 프로세스에서만) — 없으면 None
    if conn is None:
        conn = llm_connections.role_conn(role) if role else llm_connections.active()
    if conn:  # 등록된 기본 연결이 최우선 — 내부 vLLM 이든 외부 API 든 같은 경로로 쓴다
        return {**_options(conn),
            "base_url": conn["base_url"],
            "model": conn.get("model", ""),
            "vision_model": conn.get("vision_model", ""),
            "api_key": llm_connections.resolve_key(conn) or "dummy",
            "configured": True,
            "source": "연결: " + conn["name"] + (f" ({conn['failover_from']} 응답 없음, 넘김)" if conn.get("failover_from") else ""),
            "kind": conn["kind"], "external": llm_connections._is_external(conn),
            "connection_id": conn["id"],
        }
    env_url = os.environ.get("VLLM_BASE_URL", "")
    base_url = _override.get("base_url") or env_url or DEFAULT_BASE_URL
    return {**_options({}),
        "base_url": base_url,
        "model": _override.get("model") or os.environ.get("VLLM_MODEL", ""),
        "api_key": os.environ.get("VLLM_API_KEY", "dummy"),
        "configured": bool(_override.get("base_url") or env_url),
        "source": "설정" if _override.get("base_url") else ("환경변수" if env_url else "기본값"),
        "kind": "vllm", "external": False, "connection_id": "",
    }


def _json_opt(raw):
    """연결 설정 값 또는 환경변수 글(JSON) → dict. 「none」·「{}」 는 「보내지 않음」(빈 dict), 없거나 못 읽으면 None(자동)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return dict(raw)
    t = str(raw).strip()
    if t.lower() in ("none", "off", "{}"):
        return {}
    try:
        import json

        v = json.loads(t)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _options(conn: dict) -> dict:
    """모델 계열마다 다른 요청 규약 — 연결 설정이 있으면 그것, 없으면 환경변수, 둘 다 없으면 자동(None·「auto」).

    no_think    생각 끄기 추가 인자(extra_body). 자동이면 vLLM 은 chat_template_kwargs.enable_thinking=false, Ollama 는 reasoning_effort=none
    think       생각 켜기 추가 인자(ZZAIMY_ANSWER_THINK=1 일 때 답 쓰기에만). 자동이면 no_think 가 자동일 때만 chat_template_kwargs 식
    structured  구조화 출력(response_format json_schema)을 쓰나 — auto(쓰고, 서버가 거절하면 JSON 지시+파싱으로 물러남)·
                json_schema(물러나지 않음)·prompt(처음부터 JSON 지시+파싱)
    context     서버가 문맥 한도(max_model_len)를 알려 주지 않을 때 쓸 값(0 이면 ZZAIMY_WRITER_CONTEXT)
    """
    env = os.environ
    no_think = _json_opt(conn.get("no_think")) if "no_think" in conn else _json_opt(env.get("ZZAIMY_LLM_NO_THINK"))
    think = _json_opt(conn.get("think")) if "think" in conn else _json_opt(env.get("ZZAIMY_LLM_THINK"))
    structured = str(conn.get("structured") or env.get("ZZAIMY_LLM_STRUCTURED") or "auto").strip().lower()
    if structured not in ("auto", "json_schema", "prompt"):
        structured = "auto"
    try:
        context = int(conn.get("context") or 0)
    except (TypeError, ValueError):
        context = 0
    return {"no_think": no_think, "think": think, "structured": structured, "context": context}


_status_cache: dict = {"at": 0.0, "key": "", "result": None}


def status(ttl: float = 60.0) -> dict:
    """화면 공통 표시용 — probe 결과를 ttl 초 동안 재사용한다(주소 미설정이면 묻지 않는다)."""
    import time

    cfg = current()
    if not cfg["configured"]:
        return {"ok": False, "configured": False, "model": "", "models": [], "error": "주소 미설정",
                "external": False, "source": cfg["source"]}
    key = f"{cfg['base_url']}|{cfg['model']}|{cfg.get('connection_id', '')}"
    now = time.time()
    if _status_cache["result"] is None or _status_cache["key"] != key:
        r = probe(timeout=2.0)
        _status_cache.update(at=now, key=key, result=r)
    elif now - _status_cache["at"] > ttl and not _status_cache.get("refreshing"):
        # 오래된 값은 그대로 보여 주고 뒤에서 다시 묻는다 — 60초마다 그 순간 화면을 연 사람이 1.9초를 기다렸다(10-28)
        import threading
        _status_cache["refreshing"] = True

        def _refresh() -> None:
            try:
                _status_cache.update(at=time.time(), key=key, result=probe(timeout=2.0))
            finally:
                _status_cache["refreshing"] = False
        threading.Thread(target=_refresh, name="model-status", daemon=True).start()
    r = _status_cache["result"]
    model = cfg["model"] or (r["models"][0] if r["models"] else "")
    return {"ok": r["ok"], "configured": True, "model": model, "models": r["models"], "error": r["error"],
            "external": bool(cfg.get("external")), "source": cfg["source"]}


def reset_status_cache() -> None:
    _status_cache.update(at=0.0, key="", result=None, refreshing=False)


def probe(base_url: str | None = None, timeout: float = 3.0) -> dict:
    """서버의 모델 목록을 물어본다 — 실패해도 예외 없이 사유만 돌려준다."""
    cfg = current()
    url = base_url or cfg["base_url"]
    try:
        from openai import OpenAI

        client = OpenAI(base_url=url, api_key=cfg["api_key"], timeout=timeout, max_retries=0)
        models = [m.id for m in client.models.list().data]
        return {"ok": True, "models": models, "error": ""}
    except Exception as e:  # 연결 실패·인증 실패 등 — 화면에는 사유 한 단어만
        name = type(e).__name__
        if "Connection" in name or "Timeout" in name:
            reason = "연결되지 않음"
        elif "ModuleNotFound" in name or "Import" in name:
            reason = "openai 패키지 없음"
        else:
            reason = name
        return {"ok": False, "models": [], "error": reason}


# ---------------- 사용량 ----------------

_usage_path = None
_usage_lock = __import__("threading").Lock()


def configure_usage(path) -> None:
    global _usage_path
    _usage_path = path


def _usage_load() -> dict:
    import json
    from pathlib import Path

    if _usage_path and Path(_usage_path).is_file():
        try:
            return json.loads(Path(_usage_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def record_usage(connection_id: str, model: str, usage) -> None:
    """응답의 usage(prompt·completion 토큰)를 날짜·연결·모델별로 더한다. 실패해도 호출을 막지 않는다."""
    import json
    from datetime import date
    from pathlib import Path

    if _usage_path is None or usage is None:
        return
    try:
        p_tok = int(getattr(usage, "prompt_tokens", None) or (usage.get("prompt_tokens") if isinstance(usage, dict) else 0) or 0)
        c_tok = int(getattr(usage, "completion_tokens", None) or (usage.get("completion_tokens") if isinstance(usage, dict) else 0) or 0)
    except (TypeError, ValueError):
        return
    key = connection_id or "env"
    with _usage_lock:
        data = _usage_load()
        day = data.setdefault(date.today().isoformat(), {})
        row = day.setdefault(key, {}).setdefault(model or "?", {"requests": 0, "prompt": 0, "completion": 0})
        row["requests"] += 1
        row["prompt"] += p_tok
        row["completion"] += c_tok
        try:
            Path(_usage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(_usage_path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


def usage_today(connection_id: str = "") -> dict:
    """오늘 요청 수와 토큰 합계 — 연결 id 를 주면 그 연결만, 비우면 전체."""
    from datetime import date

    day = _usage_load().get(date.today().isoformat(), {})
    out = {"requests": 0, "prompt": 0, "completion": 0, "total": 0}
    for key, models in day.items():
        if connection_id and key != connection_id:
            continue
        for row in models.values():
            out["requests"] += row.get("requests", 0)
            out["prompt"] += row.get("prompt", 0)
            out["completion"] += row.get("completion", 0)
    out["total"] = out["prompt"] + out["completion"]
    return out
