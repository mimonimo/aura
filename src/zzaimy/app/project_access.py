"""프로젝트 공유 — 누가 이 프로젝트를 볼 수 있고 함께 작업할 수 있나(10-21 사용자).

공유 범위는 프로젝트마다 담당자가 정한다: 나만 · 부서(일괄) · 지정한 사용자. 권한은 셋이다.
- 소유: 만든 사람(projects.owner). 프로젝트 이름·지우기·보관·공유 설정·문서 추가·작성 원칙은 소유자와 관리자만
- 함께 작업(level "edit"): 그 프로젝트 안에 자기 대화를 열어 묻고 문서를 쓴다(드라이브 작업 문서는 편집 권한)
- 참조(level "view", 옛 이름 「보기」): 프로젝트 화면·공개 대화·작업 문서를 읽기 전용으로(드라이브 작업 문서는 보기 권한).
  대화 시작·문서 추가·원칙 수정·작업본 쓰기는 막힌다
저장 값 "view"·"edit" 는 예전 그대로다(이름만 「보기」→「참조」) — 옛 기록을 옮길 일이 없다.
사업 담당자(메인 담당자)는 설정 project_lead:<프로젝트 id> = 계정(없으면 만든 사람), 머리 아이콘 색은 project_color:<프로젝트 id> = 팔레트 열쇠.
저장은 설정 project_shares:<프로젝트 id> = {"mode": "private"|"shared", "items": [{"to": "dept:<부서>"|"user:<계정>", "level": "view"|"edit"}]}.
설정이 없는 옛 프로젝트는 지금까지와 같게 — 담당자 부서에 「보기」(10-16 같은 부서 동료 읽기 전용).
받는 사람은 교직원 계정만(학생은 프로젝트를 쓰지 않는다). 대화 하나하나의 공개는 따로(비공개 대화는 만든 사람만 — chat_sessions.is_private).
"""
from __future__ import annotations

import json

LEVELS = ("view", "edit")
LEVEL_LABEL = {"view": "참조", "edit": "함께 작업"}
LEVEL_HINT = {"view": "읽기 전용", "edit": "읽기·쓰기"}

# 프로젝트 아이콘 색 — 얇은 동그라미 팔레트. 값은 (아이콘 색, 바탕 색)
PALETTE = {
    "navy": ("#1B3B75", "#E8EFFC"), "blue": ("#2563EB", "#E6EEFE"), "teal": ("#0F766E", "#E3F4F1"),
    "green": ("#3F7F3A", "#E9F4E6"), "amber": ("#B45309", "#FBF0DF"), "red": ("#B42318", "#FCE9E7"),
    "plum": ("#7E3A8E", "#F4E8F7"), "slate": ("#475569", "#EDF0F4"),
}
DEFAULT_COLOR = "navy"


def color_key(db, project_id: int) -> str:
    """이 프로젝트 아이콘 색의 팔레트 열쇠 — 정하지 않았으면 기본(navy)."""
    v = (db.get_setting(f"project_color:{int(project_id)}", "") or "").strip()
    return v if v in PALETTE else DEFAULT_COLOR


def color(db, project_id: int) -> str:
    """아이콘 색(#RRGGBB) — 목록·사이드바가 같은 프로젝트를 같은 색으로 그린다."""
    return PALETTE[color_key(db, project_id)][0]


def set_color(db, project_id: int, key: str) -> str:
    if key not in PALETTE:
        raise ValueError("없는 색입니다")
    db.set_setting(f"project_color:{int(project_id)}", key)
    return key


def lead(db, project: dict, accounts: dict | None = None) -> str:
    """사업 담당자 계정 — 정하지 않았으면 만든 사람. accounts 를 주면 지금도 「함께 작업」 대상(활성 계정)인지 다시 본다 —
    공유에서 빠졌거나 부서를 옮겼거나 계정이 꺼진 사람의 옛 지정이 권한(진행 현황 등)으로 남지 않게 만든 사람으로 돌린다."""
    owner = project.get("owner") or ""
    v = (db.get_setting(f"project_lead:{int(project['id'])}", "") or "").strip()
    if not v:
        return owner
    if accounts is not None:
        a = accounts.get(v) or {}
        if not a or not a.get("active", True) or v not in lead_choices(project, shares(db, project), accounts):
            return owner
    return v


def lead_choices(project: dict, share: dict, accounts: dict) -> list[str]:
    """사업 담당자로 고를 수 있는 사람 — 만든 사람과 「함께 작업」으로 받는 교직원(참조는 읽기 전용이라 뺀다)."""
    owner = project.get("owner") or ""
    rec = recipients(share, accounts, owner) if share.get("mode") == "shared" else {}
    return [owner, *sorted(u for u, lv in rec.items() if lv == "edit")] if owner else sorted(u for u, lv in rec.items() if lv == "edit")


def set_lead(db, project: dict, user: str, choices: list[str]) -> str:
    if user not in choices:
        raise ValueError("공유 대상 가운데서 고릅니다")
    db.set_setting(f"project_lead:{int(project['id'])}", "" if user == project.get("owner") else user)
    return user


def can_write(level: str) -> bool:
    """대화 시작·문서 추가·원칙 수정·작업본 쓰기를 할 수 있는 권한인가 — 소유자·관리자(edit)·함께 작업(edit)만. 참조(view)는 못 한다."""
    return level == "edit"


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
    """화면 한 줄 — 「나만 보기」 · 「기획처(참조) · test(함께 작업)」."""
    if share.get("mode") == "private" or not share.get("items"):
        return "나만 보기"
    return " · ".join(f"{i['to'].split(':', 1)[1]}({LEVEL_LABEL.get(i['level'], i['level'])})" for i in share["items"])
