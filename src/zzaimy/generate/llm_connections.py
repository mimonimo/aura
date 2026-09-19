"""LLM 연결 관리 — 교내 GPU 서버와 외부 GPU 서버(OpenAI 호환 규격 API)를 등록하고 용도를 정한다.

- 상용 클라우드 API(OpenAI·Anthropic 등)는 쓰지 않는다. '외부'는 교내망 밖의 GPU 서버를 뜻한다.
- 저장: data/platform/llm_connections.json (0600). API 키는 이 파일에만 있고 화면에는 끝 4자리만 보인다.
- 문서 작업(채팅·검토·초안·요약)의 기본 연결: 교내 서버는 바로, 외부 서버는 개인정보 검토 확인(ack) 후에 지정한다.
- 외부 AI 참조(민감정보 제거 후 일반 지식 질의, ADR-0008)는 외부 서버 연결만 쓴다.
- 값이 하나도 없으면 model_config 의 예전 경로(설정 > 환경변수 > 기본값)를 그대로 쓴다.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

KINDS = {
    "vllm": {"label": "교내 GPU 서버", "external": False, "base_url": "http://<교내 GPU 서버>:8000/v1"},
    "partner": {"label": "외부 GPU 서버", "external": True, "base_url": "https://<외부 GPU 서버 주소>/v1"},
}

_path: Path | None = None
_cache: dict | None = None


def configure(path: Path) -> None:
    """앱 기동 때 저장 위치를 정한다(data/platform 옆)."""
    global _path, _cache
    _path = Path(path)
    _cache = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = {"connections": [], "active": "", "external": ""}
    if _path and _path.is_file():
        try:
            data = json.loads(_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    data.setdefault("connections", [])
    data.setdefault("active", "")
    data.setdefault("external", "")
    _cache = data
    return data


def _save(data: dict) -> None:
    global _cache
    _cache = data
    if _path is None:
        return
    _path.parent.mkdir(parents=True, exist_ok=True)
    _path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        _path.chmod(0o600)
    except OSError:
        pass


def mask_key(key: str) -> str:
    key = key or ""
    if not key:
        return "없음"
    return ("…" + key[-4:]) if len(key) > 4 else "설정됨"


def public(conn: dict) -> dict:
    """화면용 사본 — 키는 마스킹."""
    c = {k: v for k, v in conn.items() if k != "api_key"}
    c["catalog_n"] = len(conn.get("catalog") or [])
    c["catalog_at"] = conn.get("catalog_at", "")
    c["options"] = catalog_options(conn)
    c["api_key_masked"] = mask_key(conn.get("api_key", ""))
    c["has_key"] = bool(conn.get("api_key"))
    c["kind_label"] = KINDS.get(conn.get("kind", ""), {}).get("label", conn.get("kind", ""))
    return c


def list_public() -> list[dict]:
    data = _load()
    return [dict(public(c), active=(c["id"] == data["active"]), external_use=(c["id"] == data["external"]))
            for c in data["connections"]]


def get(cid: str) -> dict | None:
    for c in _load()["connections"]:
        if c["id"] == cid:
            return c
    return None


def active() -> dict | None:
    data = _load()
    return get(data["active"]) if data["active"] else None


def active_id() -> str:
    return _load()["active"]


def add(name: str, kind: str, base_url: str, model: str, api_key: str, vision_model: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError("연결 종류가 올바르지 않습니다")
    name = name.strip()[:60]
    if not name:
        raise ValueError("연결 이름을 적어 주세요")
    base_url = base_url.strip() or KINDS[kind]["base_url"]
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("주소는 http(s):// 로 시작해야 합니다")
    if KINDS[kind]["external"] and not base_url.startswith("https://"):
        raise ValueError("외부 서버 주소는 https:// 여야 합니다")
    conn = {
        "id": secrets.token_hex(4), "name": name, "kind": kind,
        "external": KINDS[kind]["external"], "base_url": base_url,
        "model": model.strip()[:120], "vision_model": vision_model.strip()[:120], "api_key": api_key.strip(),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    data = _load()
    data["connections"].append(conn)
    _save(data)
    return conn


def update(cid: str, name: str = "", base_url: str = "", model: str = "", api_key: str = "",
           vision_model: str = "") -> dict:
    """빈 값은 그대로 둔다(키는 비워 보내면 유지, 'clear' 를 보내면 지운다)."""
    data = _load()
    conn = get(cid)
    if conn is None:
        raise ValueError("없는 연결입니다")
    if name.strip():
        conn["name"] = name.strip()[:60]
    if base_url.strip():
        if not base_url.strip().startswith(("http://", "https://")):
            raise ValueError("주소는 http(s):// 로 시작해야 합니다")
        if conn["external"] and not base_url.strip().startswith("https://"):
            raise ValueError("외부 서버 주소는 https:// 여야 합니다")
        conn["base_url"] = base_url.strip()
    conn["model"] = model.strip()[:120]
    conn["vision_model"] = vision_model.strip()[:120]
    if api_key.strip() == "clear":
        conn["api_key"] = ""
    elif api_key.strip():
        conn["api_key"] = api_key.strip()
    _save(data)
    return conn


def delete(cid: str) -> None:
    data = _load()
    data["connections"] = [c for c in data["connections"] if c["id"] != cid]
    if data["active"] == cid:
        data["active"] = ""
    if data.get("external") == cid:
        data["external"] = ""
    _save(data)


def activate(cid: str, ack_external: bool = False) -> dict:
    """문서 작업의 기본 연결 지정. 외부 서버는 개인정보 검토 확인(ack) 없이는 지정되지 않는다."""
    conn = get(cid)
    if conn is None:
        raise ValueError("없는 연결입니다")
    if conn["external"] and not ack_external:
        raise ValueError("외부 서버를 문서 작업에 쓰려면 개인정보 검토 확인에 체크해야 합니다")
    data = _load()
    data["active"] = cid
    if conn["external"]:
        data["ack_external_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save(data)
    return conn


def deactivate() -> None:
    data = _load()
    data["active"] = ""
    _save(data)


def set_external(cid: str) -> dict:
    """외부 AI 참조(이그레스 게이트웨이)가 쓸 연결 지정 — 외부 서버만."""
    conn = get(cid)
    if conn is None:
        raise ValueError("없는 연결입니다")
    if not conn["external"]:
        raise ValueError("교내 서버는 외부 AI 참조용으로 지정할 수 없습니다")
    data = _load()
    data["external"] = cid
    _save(data)
    return conn


def clear_external() -> None:
    data = _load()
    data["external"] = ""
    _save(data)


def external() -> dict | None:
    data = _load()
    return get(data["external"]) if data["external"] else None


def external_credentials() -> dict | None:
    """이그레스 게이트웨이용 — 지정된 외부 연결의 종류·주소·키·모델. 없으면 None(환경변수 경로)."""
    conn = external()
    if conn is None:
        return None
    return {"kind": conn["kind"], "base_url": conn["base_url"], "api_key": resolve_key(conn),
            "model": conn.get("model", ""), "vision_model": conn.get("vision_model", ""), "name": conn["name"]}


def resolve_key(conn: dict) -> str:
    """연결의 키 — 비어 있으면 환경변수로 대신한다(교내 서버는 보통 키가 없다)."""
    if conn.get("api_key"):
        return conn["api_key"]
    if conn.get("external"):
        return os.environ.get("ZZAIMY_PARTNER_API_KEY", "")
    return os.environ.get("VLLM_API_KEY", "dummy")


def diagnose(base_url: str, timeout: float = 4.0) -> dict:
    """주소까지의 통신을 단계별로 본다 — 주소 풀이(DNS) → 포트 연결(TCP). 어느 서버든 같은 절차."""
    import socket
    import urllib.request
    from urllib.parse import urlsplit

    u = urlsplit(base_url or "")
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    if not host:
        return {"stage": "url", "host": host, "ip": "", "port": port, "text": "주소가 비어 있음"}
    # 프록시(HTTPS_PROXY 등 환경변수)가 잡혀 있으면 SDK·urllib 도 그 길로 나가므로 진단도 프록시까지만 본다
    proxy = ""
    proxies = urllib.request.getproxies()
    if proxies.get(u.scheme) and not urllib.request.proxy_bypass(host):
        proxy = proxies[u.scheme]
    if proxy:
        pu = urlsplit(proxy if "://" in proxy else "http://" + proxy)
        ph, pp = pu.hostname or "", pu.port or 3128
        try:
            with socket.create_connection((ph, pp), timeout=timeout):
                pass
        except OSError:
            return {"stage": "proxy", "host": host, "ip": "", "port": port, "proxy": f"{ph}:{pp}",
                    "text": f"프록시에 연결되지 않음({ph}:{pp})"}
        return {"stage": "ok", "host": host, "ip": "", "port": port, "proxy": f"{ph}:{pp}", "text": ""}
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return {"stage": "dns", "host": host, "ip": "", "port": port, "text": f"주소를 찾지 못함({host})"}
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
    except OSError:
        return {"stage": "tcp", "host": host, "ip": ip, "port": port,
                "text": f"서버까지 통신이 막혀 있음({ip}:{port} 응답 없음)"}
    return {"stage": "ok", "host": host, "ip": ip, "port": port, "text": ""}


def _hint(conn: dict, net: dict) -> str:
    """실패 단계별로 담당자가 할 일 한 문장."""
    if net["stage"] == "dns":
        return "주소 철자와 이 서버의 DNS 설정을 확인하세요."
    if net["stage"] == "proxy":
        return "서비스 환경변수 HTTPS_PROXY 의 프록시가 켜져 있는지 확인하세요."
    if net["stage"] == "tcp":
        if conn.get("kind") == "partner":
            return f"이 서버에서 {net['ip']}:{net['port']} 로 나가는 통신 개방을 관리자에게 요청하세요."
        return "GPU 서버가 켜져 있는지, 주소와 포트가 맞는지 확인하세요."
    return ""


def probe(conn: dict, timeout: float = 4.0) -> dict:
    """연결 확인 — 통신 진단 뒤 모델 목록을 받아 본다. 실패해도 예외 없이 사유와 할 일만."""
    net = diagnose(conn.get("base_url", ""), timeout)
    if net["stage"] != "ok":
        return {"ok": False, "models": [], "error": net["text"], "hint": _hint(conn, net), "net": net}
    try:
        from openai import OpenAI

        client = OpenAI(base_url=conn["base_url"], api_key=resolve_key(conn) or "dummy",
                        timeout=timeout, max_retries=0)
        models = [m.id for m in client.models.list().data]
        return {"ok": True, "models": models, "error": "", "hint": "", "net": net}
    except Exception as e:
        name = type(e).__name__
        status = getattr(e, "status_code", None)
        hint = ""
        if "Authentication" in name or "Permission" in name or status in (401, 403):
            reason = "키 거부됨"
            hint = "수정 창에서 발급받은 키를 다시 입력하세요."
        elif "NotFound" in name or status == 404:
            reason = "모델 목록 경로 없음"
            hint = "주소가 /v1 로 끝나는지 확인하세요."
        elif "Connection" in name or "Timeout" in name:
            reason = "포트는 열렸으나 응답 없음"
            hint = "주소의 https·http 와 서비스 가동 여부를 확인하세요."
        elif "ModuleNotFound" in name or "Import" in name:
            reason = "openai 패키지 없음"
        elif status is not None and int(status) >= 500:
            reason = "서버 일시 불가"
            hint = "잠시 후 다시 확인하세요."
        else:
            reason = "응답을 해석하지 못함"
        return {"ok": False, "models": [], "error": reason, "hint": hint, "net": net}


def record_check(cid: str, ok: bool, text: str) -> None:
    """마지막 확인 결과를 연결에 남긴다 — 화면 표에서 배너 없이도 보이도록."""
    from datetime import datetime

    data = _load()
    for c in data["connections"]:
        if c["id"] == cid:
            c["checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            c["check_ok"] = bool(ok)
            c["check_text"] = text[:160]
    _save(data)


# ---------------- 모델 목록(카탈로그) ----------------

STATUS_LABELS = {"available": "사용 가능", "listed": "등록만 됨", "unavailable": "일시 이용 불가",
                 "retired": "중단", "disabled": "중단"}


def parse_catalog(payload) -> list[dict]:
    """허브 카탈로그(/api/catalog)나 OpenAI 호환 /v1/models 응답을 같은 모양으로 정리한다."""
    items = payload.get("data") if isinstance(payload, dict) else payload
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        out.append({
            "id": str(it["id"]), "title": str(it.get("title") or it.get("name") or it["id"]),
            "provider": str(it.get("provider") or it.get("owned_by") or ""),
            "status": str(it.get("status") or "available"),
            "modality": str(it.get("modality") or "chat"),
            "tags": [str(t) for t in (it.get("tags") or [])][:12],
            "context_length": str(it.get("context_length") or ""),
            "allow_dev_key": bool(it.get("allow_dev_key", True)),
        })
    return out


def set_catalog(cid: str, models: list[dict], source: str = "") -> dict:
    data = _load()
    conn = get(cid)
    if conn is None:
        raise ValueError("없는 연결입니다")
    conn["catalog"] = models
    conn["catalog_at"] = datetime.now().strftime("%Y-%m-%d %H:%M") + (f" · {source}" if source else "")
    _save(data)
    return conn


def catalog_options(conn: dict) -> dict:
    """모델 선택 칸용 — 글 모델(chat)과 문서 이미지 판독용(vision), 상태별로 나눠 돌려준다."""
    cat = conn.get("catalog") or []
    def pick(mods):
        rows = [m for m in cat if m.get("modality") in mods]
        return {
            "available": [m for m in rows if m.get("status") == "available" and m.get("allow_dev_key", True)],
            "other": [m for m in rows if not (m.get("status") == "available" and m.get("allow_dev_key", True))],
        }
    return {"text": pick({"chat"}), "vision": pick({"vision"})}


def _hub_catalog(conn: dict, timeout: float) -> list[dict]:
    """허브 공개 카탈로그 — 모델 설명(이름·제공자·길이·용도)을 얻는 데만 쓴다. 실패하면 빈 목록."""
    import json as _json
    import urllib.request

    base = conn["base_url"].rstrip("/")
    origin = base[:-3] if base.endswith("/v1") else base
    try:
        req = urllib.request.Request(origin + "/api/catalog", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_catalog(_json.loads(r.read().decode("utf-8")))
    except Exception:
        return []


def fetch_catalog(conn: dict, timeout: float = 8.0) -> dict:
    """모델 목록 가져오기.

    서버가 지금 실제로 내어 주는 목록(OpenAI 호환 /v1/models)을 기준으로 삼는다.
    허브 공개 카탈로그는 이름·제공자 같은 설명을 보태는 데만 쓴다. 카탈로그에만 있고
    서버가 내어 주지 않는 모델은 고르게 두지 않는다 — 고르면 호출이 실패하기 때문이다.
    """
    net = diagnose(conn.get("base_url", ""), timeout)
    if net["stage"] != "ok":
        return {"ok": False, "models": [], "source": "", "error": net["text"], "hint": _hint(conn, net)}

    described = {m["id"]: m for m in _hub_catalog(conn, timeout)}
    live = probe(conn, timeout=timeout)
    if live["ok"]:
        models = []
        for mid in live["models"]:
            row = dict(described.get(mid) or {})
            row.update({
                "id": mid,
                "title": row.get("title") or mid,
                "provider": row.get("provider", ""),
                "modality": row.get("modality", "chat"),
                "tags": row.get("tags", []),
                "context_length": row.get("context_length", ""),
                "status": "available",          # 서버가 지금 내어 주는 목록이다
                "allow_dev_key": True,
            })
            models.append(row)
        return {"ok": True, "models": models, "source": "서버 실시간 목록", "error": "", "hint": ""}

    if described:
        # 실시간 목록을 못 받았다 — 설명 목록만 보여 주되 고를 수 없게 표시한다
        models = [dict(m, status="unverified") for m in described.values()]
        return {"ok": True, "models": models, "source": "허브 설명 목록 (실시간 아님)",
                "error": "", "hint": "서버 실시간 목록을 받지 못했습니다. " + live["error"]}

    return {"ok": False, "models": [], "source": "",
            "error": f"모델 목록을 받지 못했습니다 · {live['error']}", "hint": live.get("hint", "")}


def live_models(conn: dict, timeout: float = 8.0) -> dict:
    """저장하지 않고 지금 목록만 확인한다 — 연결 추가·수정 창에서 쓴다."""
    r = probe(conn, timeout=timeout)
    if not r["ok"]:
        return {"ok": False, "models": [], "error": r["error"], "hint": r.get("hint", "")}
    described = {m["id"]: m for m in _hub_catalog(conn, timeout)}
    rows = []
    for mid in r["models"]:
        d = described.get(mid) or {}
        rows.append({"id": mid, "title": d.get("title") or mid,
                     "provider": d.get("provider", ""), "modality": d.get("modality", "chat")})
    return {"ok": True, "models": rows, "error": "", "hint": ""}
