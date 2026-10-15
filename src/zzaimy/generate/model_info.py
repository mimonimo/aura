"""생성 모델 정보 — 결과 기록에 「어느 모델이 만들었나」를 남기기 위한 것.

서빙 이름(예: 운영 Writer 의 서빙 이름)만으로는 그 이름 뒤의 가중치를 바꿔 올려도 같게 보인다. 그래서 서버가 알려 주는
실제 모델 정보를 함께 남긴다.
- vLLM: /v1/models 의 root(가중치 위치 — 마지막 이름만), parent(LoRA 어댑터면 바탕 모델의 서빙 이름), max_model_len.
  어댑터면 adapter 에 어댑터 서빙 이름을, base 에 바탕 모델의 root 를 적는다.
- Ollama: /api/tags 의 digest 앞 12자(같은 태그에 다른 가중치를 올리면 digest 가 바뀐다).
- 서버에서 못 읽으면 서빙 이름만 남는다.
서버 주소(IP·URL)와 가중치의 전체 경로는 기록에 남기지 않는다. 연결·모델마다 한 번 읽어 기억해 호출마다 묻지 않는다.
예전 기록(모델 정보 없음)은 label() 이 「모름」 으로 읽는다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

UNKNOWN = "모름"
FIELD = "llm"                       # 기록에 붙이는 필드 이름

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = threading.Lock()
_TTL_OK = 3600.0                    # 읽은 정보는 한 시간 기억한다(같은 이름에 다른 판을 올리는 일은 재기동을 동반한다)
_TTL_FAIL = 300.0                   # 못 읽었으면 5분 뒤 다시 묻는다


def _tail(path: str) -> str:
    """가중치 위치의 마지막 이름만 — 내부 경로를 기록에 남기지 않는다."""
    p = str(path or "").rstrip("/")
    return p.rsplit("/", 1)[-1] if p else ""


def _root(base_url: str) -> str:
    b = (base_url or "").rstrip("/")
    return b[:-3] if b.endswith("/v1") else b


def from_models(model: str, entries: list[dict]) -> dict:
    """/v1/models 의 항목들에서 이 서빙 이름의 정보를 고른다(서버 응답만으로 — 시험하기 쉽게 따로 둔다)."""
    by_id = {str(e.get("id") or ""): e for e in entries if isinstance(e, dict)}
    name = model or (next(iter(by_id)) if by_id else "")
    info: dict = {"name": name}
    e = by_id.get(name)
    if not e:
        return info
    parent = e.get("parent")
    if parent:                                          # vLLM LoRA 어댑터 — 어댑터와 바탕 모델을 함께
        info["adapter"] = name
        info["parent"] = str(parent)
        pe = by_id.get(str(parent)) or {}
        if pe.get("root"):
            info["base"] = _tail(pe["root"])
        if pe.get("max_model_len") and not e.get("max_model_len"):
            info["max_model_len"] = int(pe["max_model_len"])
    elif e.get("root") and _tail(e["root"]) != name:
        info["base"] = _tail(e["root"])
    if e.get("max_model_len"):
        info["max_model_len"] = int(e["max_model_len"])
    if e.get("owned_by"):
        info["engine"] = str(e["owned_by"])
    return info


def _is_ollama(base_url: str, timeout: float = 3.0) -> bool:
    """/api/version 이 답하면 Ollama — generate.client 를 들이지 않는다(DGX 판독 환경에는 그 의존이 없을 수 있다)."""
    root = _root(base_url)
    if not root.startswith(("http://", "https://")):
        return False
    try:
        with urllib.request.urlopen(root + "/api/version", timeout=timeout) as r:
            return r.status == 200 and b"version" in r.read(200)
    except Exception:
        return False


def _ollama_digest(base_url: str, model: str, timeout: float = 3.0) -> str:
    try:
        with urllib.request.urlopen(_root(base_url) + "/api/tags", timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return ""
    for m in data.get("models") or []:
        if model in (m.get("name"), m.get("model")):
            return str(m.get("digest") or "")[:12]
    return ""


def describe_url(base_url: str, model: str, api_key: str = "", *, timeout: float = 4.0) -> dict:
    """주소·모델로 직접 — 스크립트(Ollama 일괄 판정 등)용. 실패해도 예외 없이 서빙 이름만."""
    key = (_root(base_url), model or "")
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < (_TTL_OK if len(hit[1]) > 1 else _TTL_FAIL):
            return dict(hit[1])
    info: dict = {"name": model or ""}
    ollama = _is_ollama(base_url, timeout)
    if ollama:
        info["engine"] = "ollama"
        d = _ollama_digest(base_url, model, timeout)
        if d:
            info["digest"] = d
    else:
        try:
            req = urllib.request.Request(_root(base_url) + "/v1/models",
                                         headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                info = from_models(model, json.loads(r.read()).get("data") or [])
        except Exception:
            pass
    with _lock:
        _cache[key] = (now, info)
    return dict(info)


def describe(client) -> dict:
    """VllmClient(또는 같은 꼴)의 모델 정보. 서버에 묻는 것은 연결·모델마다 한 번이다."""
    model = str(getattr(client, "model", "") or "")
    api = getattr(client, "client", None)
    base = str(getattr(api, "base_url", "") or "")
    if not base:
        return {"name": model} if model else {}
    if getattr(client, "external", False) is True:
        return {"name": model}                          # 외부 연결에는 정보를 묻지 않는다 — 서빙 이름만
    key = str(getattr(api, "api_key", "") or "")
    return describe_url(base, model, key)


def stamp(client) -> dict:
    """기록에 붙일 값 — 실패하거나 클라이언트가 없으면 빈 dict(읽을 때 「모름」)."""
    if client is None:
        return {}
    try:
        return describe(client)
    except Exception:
        return {"name": str(getattr(client, "model", "") or "")} if getattr(client, "model", "") else {}


def label(info) -> str:
    """사람이 읽는 한 줄. 예전 기록(정보 없음)은 「모름」."""
    if isinstance(info, str):
        return info or UNKNOWN
    if not isinstance(info, dict) or not info.get("name"):
        return UNKNOWN
    if info.get("adapter"):
        s = f"{info['adapter']} (어댑터 · 바탕 {info.get('base') or info.get('parent') or UNKNOWN})"
    elif info.get("base"):
        s = f"{info['name']} ({info['base']})"
    else:
        s = info["name"]
    if info.get("digest"):
        s += f" @{info['digest']}"
    return s


def of(rec: dict) -> dict | None:
    """기록 한 줄의 모델 정보 — 없으면 None(모름)."""
    v = (rec or {}).get(FIELD)
    return v if isinstance(v, dict) and v.get("name") else None


def same(a, b) -> bool:
    """같은 모델이 만들었나 — 한쪽이라도 모르면 다르다고 본다(다시 볼 대상에 든다)."""
    if not isinstance(a, dict) or not isinstance(b, dict) or not a.get("name") or not b.get("name"):
        return False
    keys = ("name", "adapter", "parent", "base", "digest")
    return all((a.get(k) or "") == (b.get(k) or "") for k in keys)


def reset_cache() -> None:
    with _lock:
        _cache.clear()
