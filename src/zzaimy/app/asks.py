"""에이전트가 모르는 값을 담당자에게 묻고 답을 기억하는 일반 경로(사용자 지시 2026-09-28: 특정 값에 목매지 말고 필요한 것을 요구).

- 27B 가 절을 쓰다가 자료 어디에도 없는 값(사업단명·책임자·예산·일정·수치 …)을 asks 로 내면 chat_asks:<대화> 에 쌓인다.
- 답변 아래 선택지에 kind=form(입력 칸)으로 띄운다. 화면이 없어도 "이름: 값; 이름: 값" 문장으로 답할 수 있다.
- 답은 project_facts:<프로젝트>(프로젝트가 없으면 chat_facts:<대화>)에 기억돼 이후 모든 작성 재료([담당자가 알려 준 값])에 들어간다.
  기관 정보 이름(대학명·주소·총장·대표전화)은 기관 설정으로도 저장한다.
"""

from __future__ import annotations

import json
import re

from zzaimy.app import institution

_SEP = re.compile(r"[;\n]|,\s*(?=[^,]{1,40}?\s*(?:[:：]|은|는|이|가)\s)")
_TAIL = re.compile(r"\s*(?:입니다|이다|이고|이며|임|예요|에요)\.?$")


def pending(db, session_id: int) -> list[dict]:
    try:
        return [a for a in json.loads(db.get_setting(f"chat_asks:{session_id}", "") or "[]") if a.get("name")]
    except Exception:
        return []


def set_pending(db, session_id: int, asks: list[dict]) -> None:
    db.set_setting(f"chat_asks:{session_id}", json.dumps(asks, ensure_ascii=False) if asks else "")


def add_pending(db, session_id: int, asks: list[dict]) -> list[dict]:
    cur = pending(db, session_id)
    names = {a["name"] for a in cur}
    cur += [a for a in asks if a.get("name") and a["name"] not in names]
    set_pending(db, session_id, cur)
    return cur


def _facts_key(session_: dict | None) -> str:
    pid = (session_ or {}).get("project_id")
    return f"project_facts:{int(pid)}" if pid else f"chat_facts:{(session_ or {}).get('id', 0)}"


def facts(db, session_: dict | None) -> dict:
    """담당자가 알려 준 값 + 기관 정보(설정·문서 인출)."""
    out = {k: v for k, v in institution.facts(db).items() if v}
    try:
        out.update({k: v for k, v in json.loads(db.get_setting(_facts_key(session_), "") or "{}").items() if v})
    except Exception:
        pass
    return out


def remember(db, session_: dict | None, given: dict) -> None:
    key = _facts_key(session_)
    try:
        cur = json.loads(db.get_setting(key, "") or "{}")
    except Exception:
        cur = {}
    for k, v in given.items():
        cur[k] = v
        if k in institution.KEYS:
            institution.set_fact(db, k, v)
        elif k.replace(" ", "") in ("총장성명", "총장이름"):
            institution.set_fact(db, "총장", v)
    db.set_setting(key, json.dumps(cur, ensure_ascii=False))


def parse_pairs(text: str, names: list[str]) -> dict:
    """"사업단명: AI-X 사업단; 총괄책임자 성명은 홍길동" → {이름: 값}. 물었던 이름만 받는다(엉뚱한 문장을 값으로 오해하지 않게)."""
    out: dict = {}
    if not text or not names:
        return out
    for piece in _SEP.split(text):
        piece = (piece or "").strip()
        if not piece:
            continue
        for name in sorted(names, key=len, reverse=True):
            m = re.match(rf"\s*{re.escape(name)}\s*(?:[:：]|은|는|이|가)\s*(.+)$", piece)
            if m:
                val = _TAIL.sub("", m.group(1).strip()).strip(" .")
                if val and not re.fullmatch(r"[○◯?？]+|\(값\)|\(번호\)", val):
                    out[name] = val
                break
    return out


def form_option(asks: list[dict]) -> dict:
    """선택지 규격 kind=form — 화면은 fields 를 입력 칸으로 그리고, 채운 값을 '이름: 값; …' 문장으로 보낸다."""
    fields = [{"name": a["name"], "label": a["name"], "placeholder": a.get("hint") or "", "required": False} for a in asks[:6]]
    return {"kind": "form", "text": "필요한 값 입력", "fields": fields,
            "template": "; ".join(f"{a['name']}: {{{a['name']}}}" for a in asks[:6]),
            "question": "; ".join(f"{a['name']}: (값)" for a in asks[:6]), "submit": "기억하기"}


def compose(template: str, values: dict) -> str:
    """양식 제출 → 문장. 빈 칸의 구절은 뺀다."""
    parts = []
    for seg in re.split(r";\s*", template or ""):
        m = re.search(r"\{([^{}]+)\}", seg)
        if not m:
            continue
        v = (values.get(m.group(1)) or "").strip()
        if v:
            parts.append(seg.replace(m.group(0), v))
    return "; ".join(parts)


def render_given(given: dict) -> str:
    return ("[담당자가 알려 준 값]\n" + "\n".join(f"{k}: {v}" for k, v in given.items())) if given else ""
