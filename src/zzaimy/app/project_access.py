"""프로젝트 공유 — 누가 이 프로젝트를 볼 수 있고 함께 작업할 수 있나(10-21 사용자).

공유 범위는 프로젝트마다 담당자가 정한다: 나만 · 부서(일괄) · 지정한 사용자. 받는 사람마다 「보기」 또는 「함께 작업」.
- 보기: 프로젝트 화면·공개 대화·작업 문서를 읽기 전용으로(드라이브 작업 문서는 보기 권한)
- 함께 작업: 그 프로젝트 안에 자기 대화를 열어 묻고 문서를 쓴다(드라이브 작업 문서는 편집 권한). 프로젝트 이름·지우기·보관·공유 설정은 담당자만
저장은 설정 project_shares:<프로젝트 id> = {"mode": "private"|"shared", "items": [{"to": "dept:<부서>"|"user:<계정>", "level": "view"|"edit"}]}.
설정이 없는 옛 프로젝트는 지금까지와 같게 — 담당자 부서에 「보기」(10-16 같은 부서 동료 읽기 전용).
받는 사람은 교직원 계정만(학생은 프로젝트를 쓰지 않는다). 대화 하나하나의 공개는 따로(비공개 대화는 만든 사람만 — chat_sessions.is_private).
"""
from __future__ import annotations

import json

LEVELS = ("view", "edit")
LEVEL_LABEL = {"view": "보기", "edit": "함께 작업"}


def _key(project_id: int) -> str:
    return f"project_shares:{int(project_id)}"


def shares(db, project: dict, owner_dept: str = "") -> dict:
    """{"mode", "items"} — 설정이 없으면 담당자 부서에 보기(옛 동작)."""
    raw = db.get_setting(_key(project["id"]), "") or ""
    if raw:
        try:
            got = json.loads(raw)
            if isinstance(got, dict) and got.get("mode") in ("private", "shared"):
                items = [i for i in got.get("items") or []
                         if isinstance(i, dict) and str(i.get("to", "")).startswith(("dept:", "user:")) and i.get("level") in LEVELS]
                return {"mode": got["mode"], "items": items if got["mode"] == "shared" else []}
        except ValueError:
            pass
    d = (owner_dept or "").strip()
    # implicit — 담당자가 정하지 않은 기본값. 화면의 동료 공유 규칙(main._dept_peers: 보는 범위가 나보다 넓은 담당자의 프로젝트는 열지 않음)을 따른다
    return ({"mode": "shared", "items": [{"to": f"dept:{d}", "level": "view"}], "implicit": True} if d
            else {"mode": "private", "items": [], "implicit": True})


def set_shares(db, project_id: int, mode: str, items: list[dict]) -> dict:
    mode = "private" if mode == "private" else "shared"
    clean, seen = [], set()
    for i in items if mode == "shared" else []:
        to, lv = str(i.get("to") or "").strip(), i.get("level")
        if not to.startswith(("dept:", "user:")) or len(to) < 6 or lv not in LEVELS or to in seen:
            continue
        seen.add(to)
        clean.append({"to": to, "level": lv})
    val = {"mode": mode if clean or mode == "private" else "private", "items": clean}
    db.set_setting(_key(project_id), json.dumps(val, ensure_ascii=False))
    return val


def access(project: dict | None, *, user: str, role: str, dept: str = "", also: tuple | list = (),
           share: dict | None = None) -> str:
    """이 사람이 이 프로젝트에 갖는 권한 — "edit"(담당자·함께 작업) · "view" · ""(못 봄).
    share 는 shares() 결과(호출부가 넘긴다 — 담당자 부서를 알아야 기본값을 만든다). 관리자는 이 함수 밖에서(모든 프로젝트)."""
    if not project or not user:
        return ""
    if project.get("owner") == user:
        return "edit"
    if role not in ("staff", "professor", "head"):            # 교직원 계열만 공유를 받는다(예전 역할 이름 포함)
        return ""
    mine = {d for d in ((dept or "").strip(), *also) if d}
    best = ""
    for i in (share or {}).get("items") or []:
        to = i.get("to", "")
        hit = (to == f"user:{user}") or (to.startswith("dept:") and to[5:] in mine)
        if hit and (best == "" or i.get("level") == "edit"):
            best = i.get("level", "view")
    return best


def recipients(share: dict, accounts: dict, owner: str) -> dict[str, str]:
    """공유를 받는 계정 {계정: 권한} — 부서 공유는 그 부서(소속)의 교직원 계정 전부. 담당자·꺼진 계정·첫 비밀번호 전 계정은 뺀다.
    드라이브 작업 문서 권한(보기/편집)을 맞출 때 쓴다."""
    out: dict[str, str] = {}
    for i in share.get("items") or []:
        to, lv = i.get("to", ""), i.get("level", "view")
        if to.startswith("user:"):
            users = [to[5:]]
        else:
            users = [u for u, a in accounts.items() if ((a or {}).get("dept") or "").strip() == to[5:]]
        for u in users:
            a = accounts.get(u) or {}
            if u == owner or not a or a.get("role", "staff") not in ("staff", "professor", "head") \
                    or a.get("active") is False or a.get("must_change"):
                continue
            if out.get(u) != "edit":
                out[u] = lv
    return out


def button_label(share: dict) -> str:
    """머리줄 공유 단추 — 「공유 안 함」 · 「공유 중 · 2」(받는 부서·사람 수)."""
    if share.get("mode") == "private" or not share.get("items"):
        return "공유 안 함"
    return f"공유 중 · {len(share['items'])}"


def describe(share: dict) -> str:
    """화면 한 줄 — 「나만 보기」 · 「기획처(보기) · test(함께 작업)」."""
    if share.get("mode") == "private" or not share.get("items"):
        return "나만 보기"
    return " · ".join(f"{i['to'].split(':', 1)[1]}({LEVEL_LABEL.get(i['level'], i['level'])})" for i in share["items"])
