"""대화 입력줄의 답변 방식 — 「일반」(모델 자체 지식, 필요하면 인터넷 검색을 권한다)과 「RAG」(계정의 RAG 범위로 문서 근거 답).

- 계정마다 마지막으로 고른 방식과 고른 RAG 를 서버에 기억한다(설정 chat_mode_pref:<계정>). 처음은 RAG.
- RAG 고르기는 좁히기만 한다. 고를 수 있는 RAG 는 그 계정의 검색 범위(rag_spaces.search_scope 결과의 grant_depts)에서
  만들고, 요청·저장된 값은 늘 그 집합과 겹치는 것만 남긴다. 범위 밖 값만 왔으면 사업 문서를 쓰지 않는 쪽으로 닫는다.
- 질문마다 보낸 사람과 그때 고른 RAG 를 남긴다(설정 chat_msg_rags:<메시지>). 공유 프로젝트의 대화는 주인과 보낸 사람이
  다를 수 있어, 답은 보낸 사람이 고른 RAG 를 대화 주인의 범위와 보낸 사람의 범위 둘 다와 겹치는 것만 쓴다(narrow_shared).
- 판단(인터넷에서 찾아볼지)은 모델이 하고, 여기는 저장·교집합·표시 글만 맡는다.
"""
from __future__ import annotations

import json

MODES = ("rag", "model")          # 입력줄 토글 두 갈래 — 폼 web 값으로는 "" 와 "model"
COMMON = "공통"


def _key(user: str) -> str:
    return f"chat_mode_pref:{user or ''}"


def rag_choices(scope: dict | None, all_depts: list[str] | None = None) -> list[str]:
    """이 계정이 고를 수 있는 RAG — 검색 범위의 사업 문서 부서. 전부(None, 관리자)면 공통 + 알려진 부서.
    학생(학사 규정만)·사업 문서를 쓰지 않는 범위는 고를 것이 없다(빈 목록)."""
    if not scope or not scope.get("grant") or "levels" in scope:
        return []
    depts = scope.get("grant_depts")
    if depts is None:
        depts = [COMMON, *(all_depts or [])]
    out = [COMMON] if COMMON in depts else []
    out += [d for d in dict.fromkeys(str(x).strip() for x in depts) if d and d != COMMON]
    return out


def clean(picked, choices: list[str]) -> list[str]:
    """고른 RAG 를 고를 수 있는 것과 겹치는 것만, 고를 수 있는 순서대로."""
    want = {str(x).strip() for x in (picked or []) if str(x).strip()}
    return [c for c in choices if c in want]


def narrow(scope: dict, picked, choices: list[str]) -> dict:
    """검색 범위를 고른 RAG 로 좁힌다. 고른 것이 없으면(기본 = 전부) 그대로.
    고른 것이 하나도 고를 수 있는 것과 겹치지 않으면 사업 문서를 쓰지 않는다(넓히지 않고 닫는다).
    규정은 공통 규정이 늘 들어가므로(db.list_regulation_chunks), 부서 규정만 고른 부서로 좁힌다."""
    raw = [str(x).strip() for x in (picked or []) if str(x).strip()]
    if not raw or not choices:
        return scope
    keep = clean(raw, choices)
    if keep and set(keep) == set(choices):
        return scope
    out = dict(scope)
    out["grant_depts"] = keep
    if not keep:
        out["grant"] = False
    others = [d for d in keep if d != COMMON]
    out.pop("reg_depts", None)
    if len(others) > 1:
        out["reg_depts"] = others
        out["dept"] = others[0]
    else:
        out["dept"] = others[0] if others else COMMON
    out.setdefault("user", scope.get("user") or "")
    out["space"] = f"{scope.get('space', '')}|pick:{','.join(keep) or '-'}"
    return out


def narrow_shared(owner_scope: dict, sender_scope: dict | None, picked, all_depts: list[str] | None = None) -> dict:
    """보낸 사람이 고른 RAG 로 대화 주인의 범위를 좁힌다 — 보낸 사람이 고를 수 있는 것 안에서, 다시 주인의 범위 안에서만(넓히지 않는다).
    고른 것이 없으면(기본 = 전부) 보낸 사람이 고를 수 있는 전부. 보낸 사람 범위와 하나도 겹치지 않으면 사업 문서를 쓰지 않는다."""
    sender_choices = rag_choices(sender_scope, all_depts)
    raw = [str(x).strip() for x in (picked or []) if str(x).strip()]
    want = clean(raw, sender_choices) if raw else list(sender_choices)
    owner_choices = rag_choices(owner_scope, all_depts)
    if not want:
        if not owner_choices:
            return owner_scope
        return narrow(owner_scope, ["\x00"], owner_choices)       # 겹치는 것 없음 — 닫는다
    return narrow(owner_scope, want, owner_choices)


def _msg_key(mid: int) -> str:
    return f"chat_msg_rags:{int(mid)}"


def remember_pick(db, mid: int, user: str) -> None:
    """질문 하나에 보낸 사람과 그때 고른 RAG(빈 목록 = 전부)를 남긴다 — 답 만들기가 대화 주인이 아니라 보낸 사람의 선택을 쓰게."""
    db.set_setting(_msg_key(mid), json.dumps({"by": user or "", "rags": load(db, user)["rags"]}, ensure_ascii=False))


def message_pick(db, mid: int | None) -> dict | None:
    """remember_pick 으로 남긴 값 {by, rags}. 없거나 깨졌으면 None."""
    if not mid:
        return None
    try:
        v = json.loads(db.get_setting(_msg_key(mid), "") or "null")
    except ValueError:
        return None
    if not isinstance(v, dict) or not isinstance(v.get("by"), str):
        return None
    return {"by": v["by"], "rags": [str(x) for x in v.get("rags") or [] if isinstance(x, str)]}


def label(picked: list[str], choices: list[str]) -> str:
    """대화에 보일 짧은 이름 — 전부면 빈 글(그냥 「RAG」), 좁혔으면 「공통·기획처」."""
    keep = clean(picked, choices)
    if not keep or set(keep) == set(choices):
        return ""
    return "·".join(keep)


def load(db, user: str) -> dict:
    """계정의 저장된 선택 {mode, rags}. 없거나 깨졌으면 RAG·전부."""
    try:
        v = json.loads(db.get_setting(_key(user), "") or "{}")
    except ValueError:
        v = {}
    if not isinstance(v, dict):
        v = {}
    mode = v.get("mode") if v.get("mode") in MODES else "rag"
    rags = [str(x) for x in v.get("rags") or [] if isinstance(x, str)]
    return {"mode": mode, "rags": rags}


def save(db, user: str, *, mode: str | None = None, rags=None, choices: list[str] | None = None) -> dict:
    """방식·고른 RAG 를 저장한다(주어진 것만). RAG 는 고를 수 있는 것과 겹치는 것만, 전부 고르면 빈 목록(= 기본 전부)."""
    cur = load(db, user)
    if mode in MODES:
        cur["mode"] = mode
    if rags is not None:
        keep = clean(rags, choices or [])
        cur["rags"] = [] if set(keep) == set(choices or []) else keep
    db.set_setting(_key(user), json.dumps(cur, ensure_ascii=False))
    return cur


def initial_mode(pref: dict, *, project: bool, has_files: bool, last_mode: str | None) -> str:
    """화면을 열 때 토글의 처음 값 — 이 대화에서 마지막으로 쓴 방식, 없으면 프로젝트·첨부·연결 문서가 있으면 RAG,
    아니면 계정이 마지막으로 고른 방식."""
    if last_mode == "model":
        return "model"
    if last_mode == "document":
        return "rag"
    if project or has_files:
        return "rag"
    return pref.get("mode") if pref.get("mode") in MODES else "rag"


def view(db, user: str, scope: dict | None, all_depts: list[str] | None, *, project: bool = False,
         has_files: bool = False, last_mode: str | None = None) -> dict:
    """화면에 넘길 값 {mode, choices, picked, label}. picked 가 비면 전부."""
    pref = load(db, user)
    choices = rag_choices(scope, all_depts)
    picked = clean(pref["rags"], choices)
    if set(picked) == set(choices):
        picked = []
    return {"mode": initial_mode(pref, project=project, has_files=has_files, last_mode=last_mode),
            "choices": choices, "picked": picked, "label": label(picked, choices)}
