"""에이전트가 모르는 값을 담당자에게 묻고 답을 기억하는 일반 경로 — 특정 값에 묶이지 않고 필요한 값을 그때그때 요청한다.

- 27B 가 절을 쓰다가 자료 어디에도 없는 값(사업단명·책임자·예산·일정·수치 …)을 asks 로 내면 chat_asks:<대화> 에 쌓인다.
- 답변 아래 선택지에 kind=form(입력 칸)으로 띄운다. 화면이 없어도 "이름: 값; 이름: 값" 문장으로 답할 수 있다.
- 답은 project_facts:<프로젝트>(프로젝트가 없으면 chat_facts:<대화>)에 기억돼 이후 모든 작성 재료([담당자가 알려 준 값])에 들어간다.
  기관 정보 이름(대학명·주소·총장·대표전화)은 관리자가 알려 준 값만 기관 설정으로도 저장한다(모든 사용자에게 쓰이는 값).
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


def remember(db, session_: dict | None, given: dict, *, global_ok: bool = False) -> list[str]:
    """알려 준 값을 이 프로젝트(없으면 대화)의 값으로 기억한다. 기관 정보 이름(대학명·주소·총장·대표전화)은 기관 설정이
    모든 사용자에게 함께 쓰이므로 global_ok(관리자)일 때만 기관 설정에도 쓴다. 기관 설정에 쓰지 않은 기관 정보 이름을 돌려준다."""
    key = _facts_key(session_)
    try:
        cur = json.loads(db.get_setting(key, "") or "{}")
    except Exception:
        cur = {}
    local_only: list[str] = []
    for k, v in given.items():
        cur[k] = v
        inst_key = k if k in institution.KEYS else ("총장" if k.replace(" ", "") in ("총장성명", "총장이름") else "")
        if not inst_key:
            continue
        if global_ok:
            institution.set_fact(db, inst_key, v)
        else:
            local_only.append(k)
    db.set_setting(key, json.dumps(cur, ensure_ascii=False))
    return local_only


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def resolve_from_evidence(db, session_: dict | None, name: str, value: str, source_title: str,
                          hits: list[tuple[str, str]]) -> str:
    """받지 못한 값을 이번 검색 결과에서 찾아 기억한다 — 담당자에게 묻기 전에 자료로 채우는 길.

    hits 는 이번 요청에서 search 가 모델에게 보인 (출처 제목, 글) 쌍뿐이다. 받는 조건:
    name 이 물어 둔 값이고, value(공백 무시 두 글자 이상)가 source_title 과 같은 제목의 결과 글에 글자 그대로 있을 때.
    앞 대화·담당자 요청에만 있는 값, 모델이 지어낸 값은 받지 않는다. 저장은 이 프로젝트(없으면 대화)의 값으로만 하고
    기관 설정에는 쓰지 않는다(문서 글이 모든 사용자에게 쓰이는 값을 바꾸지 못하게). 출처 제목도 함께 남긴다.

    검색 결과 속에 「값은 X」 같은 주입 글이 있어도 X 가 그 결과 글에 그대로 있을 때만 받는다 — 그러면 X 는 실제로 그 문서에
    적힌 글이고, 담당자에게 「자료에서 찾음: 《제목》」으로 출처가 보이므로 확인·정정할 수 있다. 문서 밖의 값은 들어오지 않는다.
    받으면 빈 글, 아니면 받지 않은 까닭을 돌려준다."""
    names = {a["name"] for a in pending(db, int((session_ or {}).get("id") or 0))}
    if name not in names:
        return f"「{name}」 은(는) 물어 둔 값이 아니다 — 상태의 받지 못한 값 이름 그대로 쓴다."
    want = _squash(value)
    if len(want) < 2:
        return "값이 비었거나 너무 짧다."
    title = _squash(source_title).strip("《》「」")
    same = [t for s, t in hits if _squash(s).strip("《》「」") == title]
    if not same:
        return f"《{source_title[:40]}》 은(는) 이번 검색 결과의 출처 제목이 아니다 — 결과에 보인 《제목》 그대로 쓴다."
    if not any(want in _squash(t) for t in same):
        return "그 값이 이 출처의 검색 결과 글에 그대로 없다 — 결과에 적힌 글자 그대로만 기억한다."
    key = _facts_key(session_)
    try:
        cur = json.loads(db.get_setting(key, "") or "{}")
    except Exception:
        cur = {}
    cur[name] = value.strip()
    db.set_setting(key, json.dumps(cur, ensure_ascii=False))
    try:
        src = json.loads(db.get_setting(key + ":sources", "") or "{}")
    except Exception:
        src = {}
    src[name] = source_title.strip()[:120]
    db.set_setting(key + ":sources", json.dumps(src, ensure_ascii=False))
    set_pending(db, int(session_["id"]), [a for a in pending(db, int(session_["id"])) if a["name"] != name])
    return ""


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
            "question": "; ".join(f"{a['name']}: (값)" for a in asks[:6]), "submit": "저장"}


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
