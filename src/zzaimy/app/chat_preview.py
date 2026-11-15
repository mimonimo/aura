"""대화창 미리 보기·선택 카드 — 에이전트가 문서에 넣거나 바꿀 것을 대화창에 그 모양 그대로 보인다.

세 갈래가 같은 미리 보기 자료(preview)를 쓴다.
- 선택 카드(choice): 담당자가 골라야 할 때 후보를 나란히 보인다. 처음부터 묻는 것(ask_user 의 choices)과, 먼저 반영한 뒤 붙이는
  「바꾸기」 후보(finish 의 alternatives) 두 길이 같은 꼴이다. 고르면 「머리: 2번(이름)」 글이 다음 요청으로 들어가고,
  고른 선택지의 모양 값은 대화의 「고른 것」 으로 남아 에이전트가 서식 도구(hwp_style·hwp_text_style·hwp_write) 인자로 쓴다.
- 반영 미리 보기(done): 고를 것 없이 반영한 작업을 답 아래에 작게 보인다 — 도구 결과(무엇을 썼는지)에서만 만든다(모델이 따로 짓지 않는다).
- 값 입력 카드(asks.form_option)는 같은 카드 꼴(머리·몸·발)을 쓴다.

preview 갈래(type): font(글꼴·크기 견본 문장) · text(문단 앞부분) · table(머리줄 1~2줄 + 앞 몇 행, 나머지는 「외 N행」, 모양 값 style,
배치 값 layout) · image(같은 사이트 안 그림 주소) · page(용지·방향·여백) · change(바뀌기 전과 뒤 글). 그 밖의 갈래는 버린다.
표 모양 값(style)의 열쇠는 hwp_style 인자 이름 그대로다 — 고른 값을 그대로 넘기면 된다.
특정 사업·문서 이름이나 사례 규칙을 두지 않는다.
"""
from __future__ import annotations

import json
import re
from typing import Callable

QUESTIONS_MAX = 4
OPTIONS_MIN, OPTIONS_MAX = 2, 4
ROWS_SHOWN = 4                      # 표 미리 보기에 그리는 자료 행 — 나머지는 「외 N행」
COLS_MAX = 8
CELL_MAX = 40
TEXT_MAX = 400
DONE_MAX = 6                        # 답 하나에 붙이는 반영 미리 보기 수(칸·맡긴 절 글·표·표 칸이 함께 보이게)

SHAPE_TYPES = ("font", "table", "text", "page", "change")   # 모양·배치를 보이는 갈래 — 먼저 묻기 관문(gate_first)이 본다
STYLE_KEYS = ("head_fill", "label_fill", "outer", "inner", "head_bottom", "font_pt", "head_bold", "head_align", "body_align")
ALIGNS = ("가운데", "왼쪽", "오른쪽", "양쪽")
PLACES = ("이 절 안", "별도 표", "서식 표 채우기")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_NAMED = {"회색": "#E5E5E5", "연회색": "#F2F2F2", "연파랑": "#DCE6F2", "연남색": "#DCE6F2", "없음": ""}


# 미리 보기 그림으로 받는 같은 사이트 주소의 앞부분 — 문서·기안기·첨부 그림만(그 밖은 받지 않는다)
IMAGE_PREFIXES = ("/hwp/", "/doc/", "/static/", "/files/")


def _same_site_path(v) -> str:
    """같은 사이트의 정해진 경로만 — 역슬래시(브라우저가 / 로 읽어 「/\\evil.com」 이 「//evil.com」 이 된다)·제어 문자·
    공백·따옴표·꺾쇠가 섞였거나, 구문 분석해 scheme·netloc 이 있거나, 경로가 「//」 로 시작하면 빈 글."""
    from urllib.parse import urlsplit
    s = str(v or "").strip()
    if not s or len(s) > 300 or any(c in s for c in "\\\"'<> ") or any(ord(c) < 32 or ord(c) == 127 for c in s):
        return ""
    u = urlsplit(s)
    if u.scheme or u.netloc or not u.path.startswith("/") or u.path.startswith("//"):
        return ""
    return s if u.path.startswith(IMAGE_PREFIXES) else ""


def _s(v, n: int) -> str:
    t = re.sub(r"[ \t]+", " ", str(v if v is not None else "")).strip()
    return t[:n]


def _cells(row, n: int = COLS_MAX) -> list[str]:
    if not isinstance(row, (list, tuple)):
        return []
    return [_s(c, CELL_MAX) for c in list(row)[:n]]


def color(v) -> str:
    """#RRGGBB 또는 이름(회색·연파랑·없음) → #RRGGBB(없음은 빈 글). 모르는 값은 빈 글."""
    t = str(v or "").strip()
    if _COLOR.match(t):
        return t.upper()
    return _NAMED.get(t, "")


def _mm(v) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(v if v is not None else ""))
    if not m:
        return None
    x = float(m.group(0))
    return x if 0 < x <= 3 else None


def norm_style(st) -> dict:
    """표 모양 값 — hwp_style 인자 이름만, 값은 갈래에 맞는 것만."""
    if not isinstance(st, dict):
        return {}
    out: dict = {}
    for k in ("head_fill", "label_fill"):
        if k in st:
            c = color(st[k])
            if c or str(st[k]).strip() == "없음":
                out[k] = c or "없음"
    for k in ("outer", "inner"):
        x = _mm(st.get(k))
        if x is not None:
            out[k] = x
    if st.get("head_bottom") is not None:
        out["head_bottom"] = "이중선" if "이중" in str(st["head_bottom"]) or str(st["head_bottom"]).upper() == "DOUBLE" else "실선"
    if st.get("font_pt") is not None:
        try:
            pt = float(st["font_pt"])
            if 6 <= pt <= 30:
                out["font_pt"] = pt
        except (TypeError, ValueError):
            pass
    if "head_bold" in st:
        out["head_bold"] = st["head_bold"] is True or str(st["head_bold"]).lower() in ("true", "1", "굵게")
    for k in ("head_align", "body_align"):
        if str(st.get(k) or "") in ALIGNS:
            out[k] = str(st[k])
    return out


def norm_layout(lay) -> dict:
    if not isinstance(lay, dict):
        return {}
    out: dict = {}
    o = str(lay.get("orient") or "")
    if o in ("행", "열"):
        out["orient"] = o
    if "total" in lay:
        out["total"] = lay["total"] is True or str(lay["total"]).lower() in ("true", "1", "있음")
    if str(lay.get("place") or "") in PLACES:
        out["place"] = str(lay["place"])
    try:
        hr = int(lay.get("head_rows") or 0)
        if hr in (1, 2):
            out["head_rows"] = hr
    except (TypeError, ValueError):
        pass
    cols = _cells(lay.get("columns"))
    if cols:
        out["columns"] = cols
    return out


def norm_preview(p) -> dict | None:
    """미리 보기 자료 → 정해진 갈래의 작은 dict. 갈래를 모르거나 비면 None."""
    if not isinstance(p, dict):
        return None
    t = str(p.get("type") or "")
    if t == "font":
        font = re.sub(r"[^\w가-힣 .\-]", "", _s(p.get("font"), 40)).strip()     # 글꼴 이름만(스타일 속성에 들어간다)
        try:
            pt = float(p.get("pt") or 0)
        except (TypeError, ValueError):
            pt = 0
        pt = pt if 6 <= pt <= 40 else 0
        if not font and not pt:
            return None
        out = {"type": "font", "font": font, "pt": pt, "bold": p.get("bold") is True,
               "sample": _s(p.get("sample"), 80) or "가나다라마바사 AURA 2026"}
        if str(p.get("align") or "") in ALIGNS:
            out["align"] = str(p["align"])
        return out
    if t == "text":
        text = str(p.get("text") or "").strip()
        if not text:
            return None
        cut = len(text) > TEXT_MAX
        return {"type": "text", "text": text[:TEXT_MAX].rstrip() + ("…" if cut else "")}
    if t == "table":
        head = p.get("head") or []
        if head and not isinstance(head[0], (list, tuple)):
            head = [head]                                            # 머리줄 한 줄을 목록 하나로 준 꼴
        head = [r for r in (_cells(h) for h in list(head)[:2]) if r]
        rows = [r for r in (_cells(x) for x in (p.get("rows") or [])) if r]
        if not head and not rows:
            return None
        try:
            more = max(0, int(p.get("more") or 0))
        except (TypeError, ValueError):
            more = 0
        more += max(0, len(rows) - ROWS_SHOWN)
        out = {"type": "table", "head": head, "rows": rows[:ROWS_SHOWN], "more": more,
               "style": norm_style(p.get("style")), "layout": norm_layout(p.get("layout"))}
        if p.get("title"):
            out["title"] = _s(p.get("title"), 60)
        return out
    if t == "image":
        src = _same_site_path(p.get("src"))
        if not src:
            return None                                              # 같은 사이트 안 정해진 미리 보기 주소만(바깥 그림·스크립트 주소를 막는다)
        return {"type": "image", "src": src[:300], "alt": _s(p.get("alt"), 60)}
    if t == "page":
        out = {"type": "page", "paper": _s(p.get("paper"), 10), "orientation": "가로" if str(p.get("orientation")) == "가로" else "세로"}
        mg = p.get("margins_mm") if isinstance(p.get("margins_mm"), dict) else {}
        out["margins_mm"] = {k: float(mg[k]) for k in ("top", "bottom", "left", "right") if isinstance(mg.get(k), (int, float))}
        if p.get("margin"):
            out["margin"] = _s(p.get("margin"), 10)
        return out
    if t == "change":
        before, after = _s(p.get("before"), 200), _s(p.get("after"), 200)
        if not before and not after:
            return None
        return {"type": "change", "before": before, "after": after, "what": _s(p.get("what"), 30)}
    return None


def norm_question(q) -> tuple[dict | None, str]:
    """질문 하나 → (정리한 질문, 문제). 선택지가 2개보다 적으면 None 과 까닭."""
    if not isinstance(q, dict):
        return None, "질문은 {question, header, options} 꼴이다."
    question = _s(q.get("question"), 120)
    header = _s(q.get("header") or question, 14)
    opts = []
    for o in (q.get("options") or [])[:OPTIONS_MAX]:
        if not isinstance(o, dict) or not _s(o.get("label"), 40):
            continue
        item = {"label": _s(o.get("label"), 40), "description": _s(o.get("description"), 120)}
        pv = norm_preview(o.get("preview"))
        if pv:
            item["preview"] = pv
        opts.append(item)
    if not question:
        return None, "question 이 비었다."
    if len(opts) < OPTIONS_MIN:
        return None, f"「{header}」 선택지가 {len(opts)}개다 — 질문마다 {OPTIONS_MIN}~{OPTIONS_MAX}개를 준다."
    out = {"question": question, "header": header, "options": opts,
           "multi": q.get("multi") is True, "allow_other": q.get("allow_other") is not False}
    kind = str(q.get("kind") or "")
    out["kind"] = "모양" if kind == "모양" or any((o.get("preview") or {}).get("type") in SHAPE_TYPES for o in opts) else "결정"
    if q.get("why"):
        out["why"] = _s(q.get("why"), 120)
    return out, ""


def norm_card(questions, mode: str = "ask") -> tuple[dict | None, str]:
    """선택 카드 → ({mode, questions}, 문제). mode: ask(처음 묻기) | change(반영 뒤 바꾸기 후보)."""
    if isinstance(questions, dict):
        questions = [questions]
    if not isinstance(questions, list) or not questions:
        return None, "질문이 비었다."
    if len(questions) > QUESTIONS_MAX:
        return None, f"질문이 {len(questions)}개다 — 한 번에 1~{QUESTIONS_MAX}개만."
    out = []
    for q in questions:
        got, why = norm_question(q)
        if got is None:
            return None, why
        out.append(got)
    return {"mode": "change" if mode == "change" else "ask", "questions": out}, ""


# ── 수치 관문 ── 미리 보기의 수치도 근거에 있는 것만(절대 규칙 1). 없는 수치가 든 칸·글은 「확인 필요」 로 바꾼다
_NUM = re.compile(r"\d[\d,.]*\d|\d")


def _ok_nums(text: str, seen: str) -> bool:
    return all(re.sub(r"\s+", "", n) in seen for n in _NUM.findall(text or ""))


def scrub_numbers(card: dict, seen_text: str) -> int:
    """표 칸·문단 견본에서 근거(seen_text)에 없는 수치를 「확인 필요」 로 — 바꾼 수를 돌려준다. 글꼴 크기·선 굵기 같은 모양 값은 보지 않는다."""
    seen = re.sub(r"\s+", "", seen_text or "")
    n = 0
    for q in card.get("questions") or []:
        for o in q.get("options") or []:
            pv = o.get("preview") or {}
            if pv.get("type") == "table":
                for rows in (pv["head"], pv["rows"]):
                    for r in rows:
                        for i, c in enumerate(r):
                            if not _ok_nums(c, seen):
                                r[i], n = "확인 필요", n + 1
            elif pv.get("type") == "text" and not _ok_nums(pv["text"], seen):
                pv["text"] = "\n".join(ln if _ok_nums(ln, seen) else "확인 필요" for ln in pv["text"].split("\n"))
                n += 1
    return n


# ── 처음부터 묻는 관문 ── 「먼저 작업, 필요하면 바꾸기 후보」. 모양·배치는 기준(작업 문서의 서식·기관 서식 프로필)이 있으면 묻지 않고
# 먼저 반영한 뒤 finish 의 alternatives 로 후보를 붙인다. 담당자가 먼저 고르겠다고 했으면 묻는다. 결정 값(무엇을 할지)은 막지 않는다
WANTS_CHOICE = r"고르|골라|선택|후보|시안|보여\s*(?:줘|주|달)|비교|어떤\s*(?:게|것이|모양|디자인)"


def gate_first(card: dict, *, has_basis: bool, wants_choice: bool) -> str:
    """처음 묻기 카드가 지금 물어도 되는가 — 막으면 모델에게 돌려줄 안내, 아니면 빈 글."""
    shape = [q["header"] for q in card.get("questions") or [] if q.get("kind") == "모양"]
    if shape and has_basis and not wants_choice:
        return ("모양·배치(" + ", ".join(f"「{h}」" for h in shape) + ")는 먼저 묻지 않는다 — 작업 문서의 서식·기관 서식·지난 문서 모양을 따라 "
                "먼저 반영한 뒤, finish 의 alternatives 에 다른 후보 2~3개를 「바꾸기」 로 붙인다. 담당자가 먼저 고르겠다고 했을 때만 먼저 묻는다.")
    return ""


def _key(text: str) -> str:
    return re.sub(r"[\s「」『』()·:：?？.]+", "", str(text or ""))


def already(card: dict, chosen: list[dict]) -> list[dict]:
    """이 대화에서 이미 고른 질문(머리나 질문 글이 같은 것)."""
    out = []
    for c in chosen:
        ck = {_key(c.get("header")), _key(c.get("question"))} - {""}
        if any({_key(q["header"]), _key(q["question"])} & ck for q in card.get("questions") or []) and c not in out:
            out.append(c)
    return out


# ── 저장 ── 카드는 그 카드를 낸 답(메시지)에 붙인다: chat_choice:<메시지 id> = {mode, questions, answers?}.
# 이 대화에서 고른 것은 chat_choices:<대화 id> = [{header, question, picked: [라벨], option: {…}, other}] — 상태 글·다시 묻기 관문이 읽는다
def _load(db, key: str, default):
    try:
        got = json.loads(db.get_setting(key, "") or "null")
    except ValueError:
        return default
    return got if isinstance(got, type(default)) else default


def card_of(db, mid: int) -> dict:
    return _load(db, f"chat_choice:{int(mid)}", {})


def save_card(db, mid: int, card: dict) -> None:
    db.set_setting(f"chat_choice:{int(mid)}", json.dumps(card, ensure_ascii=False))


def chosen(db, session_id: int) -> list[dict]:
    return _load(db, f"chat_choices:{int(session_id)}", [])


def done_of(db, mid: int) -> list[dict]:
    return _load(db, f"chat_done:{int(mid)}", [])


def save_done(db, mid: int, previews: list[dict]) -> None:
    keep = [p for p in previews if p][:DONE_MAX]
    if keep:
        db.set_setting(f"chat_done:{int(mid)}", json.dumps(keep, ensure_ascii=False))


def option_words(o: dict) -> str:
    """선택지를 한 구절로 — 이름과 설명 앞부분."""
    d = o.get("description") or ""
    return o["label"] + (f"({d[:40]})" if d else "")


def answer(db, session_id: int, mid: int, picks: dict, others: dict) -> str:
    """선택 카드의 답 → 다음 요청 글. picks {질문 번호(0부터): [선택지 번호(1부터)]}, others {질문 번호: 직접 입력 글}.
    고른 것은 카드(답한 표시)와 대화의 「고른 것」 에 남긴다. 고른 것이 없으면 빈 글."""
    card = card_of(db, mid)
    qs = card.get("questions") or []
    lines, records, answers = [], [], []
    for i, q in enumerate(qs):
        idx = [n for n in (picks.get(i) or []) if 1 <= n <= len(q["options"])]
        if not q.get("multi"):
            idx = idx[:1]
        other = _s(others.get(i), 300) if q.get("allow_other", True) else ""
        if not idx and not other:
            answers.append(None)
            continue
        words = [f"{n}번 {option_words(q['options'][n - 1])}" for n in idx]
        if other:
            words.append(f"직접 입력 「{other}」")
        lines.append(f"{q['header']}: " + ", ".join(words))
        answers.append({"picked": idx, "other": other})
        records.append({"header": q["header"], "question": q["question"], "kind": q.get("kind", ""),
                        "picked": [q["options"][n - 1]["label"] for n in idx], "other": other,
                        "options": [q["options"][n - 1] for n in idx]})
    if not lines:
        return ""
    card["answers"] = answers
    save_card(db, mid, card)
    cur = [c for c in chosen(db, session_id) if _key(c.get("header")) not in {_key(r["header"]) for r in records}]
    db.set_setting(f"chat_choices:{int(session_id)}", json.dumps((cur + records)[-12:], ensure_ascii=False))
    head = "바꾸기" if card.get("mode") == "change" else "고른 것"
    return f"[{head}] " + "; ".join(lines)


def state_lines(items: list[dict]) -> str:
    """상태 글 — 이 대화에서 담당자가 고른 것과 그 모양 값. 에이전트가 서식 도구 인자로 바로 쓴다."""
    out = []
    for c in items[-8:]:
        vals = []
        for o in c.get("options") or []:
            pv = o.get("preview") or {}
            if pv.get("type") == "table":
                if pv.get("style"):
                    vals.append("hwp_style " + json.dumps(pv["style"], ensure_ascii=False))
                if pv.get("layout"):
                    vals.append("배치 " + json.dumps(pv["layout"], ensure_ascii=False))
                if pv.get("head"):
                    vals.append("머리줄 " + " | ".join(pv["head"][0]))
            elif pv.get("type") == "font":
                vals.append("hwp_text_style " + json.dumps({k: pv[k] for k in ("font", "pt", "bold", "align") if pv.get(k)}, ensure_ascii=False))
            elif pv.get("type") == "page":
                vals.append("hwp_page_setup " + json.dumps({k: pv[k] for k in ("paper", "orientation", "margin", "margins_mm") if pv.get(k)}, ensure_ascii=False))
        picked = ", ".join(c.get("picked") or []) + (f" / 직접 입력 「{c['other']}」" if c.get("other") else "")
        out.append(f"- {c.get('header')}: {picked}" + (f" — {'; '.join(vals)}" if vals else ""))
    return "\n".join(out)


# ── 표 모양 후보 ── 선택지를 만들 때 쓰는 모양 값 목록. 지금은 기관 표 기본(house_style.TABLE)에서 변형을 만든다.
# 갈래별 디자인 자료가 생기면 set_table_looks 로 바꿔 끼운다 — (갈래 이름 또는 None) → [{label, description, style}]
TableLooks = Callable[[str | None], list[dict]]


def house_looks(kind: str | None = None) -> list[dict]:
    from zzaimy.export import house_style as hs

    base = {"head_fill": hs.PALETTE["fill"], "outer": _mm(hs.TABLE["outer"]) or 0.4, "inner": _mm(hs.TABLE["inner"]) or 0.12,
            "head_bottom": "이중선" if hs.TABLE.get("head_rule") == "DOUBLE" else "실선", "head_bold": True,
            "head_align": "가운데", "font_pt": float(hs.TYPE["table"]["pt"])}
    return [
        {"label": "기관 기본", "description": "머리줄 회색·굵게, 바깥 굵은 선", "style": dict(base)},
        {"label": "연파랑 머리줄", "description": "머리줄만 연파랑", "style": {**base, "head_fill": _NAMED["연파랑"]}},
        {"label": "선 강조", "description": "음영 없이 머리줄 아래 이중선", "style": {**base, "head_fill": "없음", "head_bottom": "이중선"}},
        {"label": "왼쪽 정렬", "description": "긴 글 칸이 많은 표", "style": {**base, "body_align": "왼쪽"}},
    ]


_LOOKS: list[TableLooks] = [house_looks]


def set_table_looks(fn: TableLooks | None) -> None:
    """표 모양 후보 공급원을 바꾼다(None 이면 기관 기본 변형으로 되돌린다)."""
    _LOOKS[0] = fn or house_looks


def table_looks(kind: str | None = None) -> list[dict]:
    try:
        got = list(_LOOKS[0](kind) or [])
    except Exception:
        got = house_looks(kind)
    return [{"label": _s(x.get("label"), 40), "description": _s(x.get("description"), 120), "style": norm_style(x.get("style"))}
            for x in got if isinstance(x, dict) and x.get("label")][:OPTIONS_MAX]


def looks_line() -> str:
    """도구 설명에 싣는 표 모양 후보 한 줄 — 「이름: 모양 값」."""
    return " / ".join(f"{x['label']} {json.dumps(x['style'], ensure_ascii=False)}" for x in table_looks())


# ── 반영 미리 보기 ── 문서를 바꾼 도구 결과에서 만든다. 성공한 걸음의 인자(실제로 넣은 글·값)만 쓴다
_MD_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_MD_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")


def md_table(text: str) -> dict | None:
    """마크다운 표 첫 개 → 표 미리 보기(머리줄 + 앞 몇 행). 표 바로 윗줄 「<표> 제목」 은 제목으로."""
    lines = (text or "").split("\n")
    for i, ln in enumerate(lines):
        if _MD_ROW.match(ln) and i + 1 < len(lines) and _MD_SEP.match(lines[i + 1]):
            head = [c.strip() for c in _MD_ROW.match(ln).group(1).split("|")]
            rows = []
            for nxt in lines[i + 2:]:
                m = _MD_ROW.match(nxt)
                if not m:
                    break
                rows.append([c.strip() for c in m.group(1).split("|")])
            title = lines[i - 1].strip() if i and lines[i - 1].strip().startswith("<표>") else ""
            return norm_preview({"type": "table", "head": [head], "rows": rows,
                                 **({"title": title.removeprefix("<표>").strip()} if title else {})})
    return None


def _plain(text: str) -> str:
    """본문 앞부분 — 표·도식 블록은 빼고 글 줄만."""
    out, fence = [], False
    for ln in (text or "").split("\n"):
        if ln.strip().startswith("```"):
            fence = not fence
            continue
        if fence or _MD_ROW.match(ln) or _MD_SEP.match(ln) or ln.strip().startswith("<표>"):
            continue
        out.append(ln.rstrip())
    return "\n".join(x for x in out if x.strip())


def from_step(action: str, args: dict, observation: str, refused: tuple[str, ...] = ()) -> list[dict]:
    """문서를 바꾼 걸음 하나 → 반영 미리 보기 [{what, preview}]. 거절·실패한 걸음은 빈 목록."""
    obs = str(observation or "").lstrip()
    if not obs or (refused and obs.startswith(refused)) or obs.startswith(("도구 실패", "같은 도구를")):
        return []
    a = args if isinstance(args, dict) else {}
    out: list[dict] = []
    if action in ("hwp_write", "write_section", "rewrite_section"):
        where = _s(a.get("field") or a.get("section"), 40)
        text = str(a.get("text") or "")
        body = _plain(text)
        if body:
            out.append({"what": f"「{where}」 글" if where else "쓴 글", "preview": norm_preview({"type": "text", "text": body})})
        tb = md_table(text)
        if tb:
            out.append({"what": f"「{where}」 표" if where else "넣은 표", "preview": tb})
    elif action == "hwp_table_edit" and str(a.get("op") or "") == "set_cells":
        vals = [[_s(v, CELL_MAX) for v in (r if isinstance(r, (list, tuple)) else [r])] for r in (a.get("values") or [])]
        vals = [r for r in vals if any(x.strip() for x in r)]
        if vals:
            no = a.get("table")
            pv = norm_preview({"type": "table", "rows": vals})
            if pv:
                out.append({"what": f"표{no} 칸" if isinstance(no, (int, str)) and str(no).strip() else "채운 표 칸", "preview": pv})
    elif action == "hwp_fill":
        vals = a.get("values") if isinstance(a.get("values"), dict) else {}
        rows = [[_s(k, CELL_MAX), _s(v, CELL_MAX)] for k, v in vals.items() if str(v or "").strip()]
        if rows:
            out.append({"what": "채운 칸", "preview": norm_preview({"type": "table", "head": [["칸", "값"]], "rows": rows})})
    elif action == "hwp_table_insert":
        vals = [r for r in (a.get("values") or []) if isinstance(r, (list, tuple))]
        head = a.get("head")
        if isinstance(head, list) and head:
            pv = norm_preview({"type": "table", "head": [head], "rows": vals})
        elif head is not False and vals:
            pv = norm_preview({"type": "table", "head": [vals[0]], "rows": vals[1:]})
        else:
            pv = norm_preview({"type": "table", "rows": vals})
        if pv:
            out.append({"what": "만든 표", "preview": pv})
    elif action == "hwp_style":
        st = norm_style(a)
        which = ", ".join(str(x) for x in (a.get("tables") or [])) or "모든 표"
        # 주지 않은 값은 기관 표 기본으로 들어간다(hwp_style 설명과 같다) — 바뀐 뒤 모양은 기본 위에 준 값을 덮은 것
        after = {**house_looks()[0]["style"], **st}
        out.append({"what": f"표 서식({which})", "preview": {"type": "table", "head": [["머리줄", "머리줄"]], "rows": [["칸", "칸"]],
                                                       "more": 0, "style": after, "layout": {}}, "changed": sorted(st)})
    elif action == "hwp_text_style":
        pv = norm_preview({"type": "font", "font": a.get("font"), "pt": a.get("pt"), "bold": a.get("bold") is True, "align": a.get("align")})
        if pv:
            sc = a.get("scope")
            where = "문서 전체" if sc in (None, "", "all") else _s((sc or {}).get("field") or (sc or {}).get("text"), 30) if isinstance(sc, dict) else ""
            out.append({"what": f"글자 모양({where})" if where else "글자 모양", "preview": pv})
    elif action == "hwp_page_setup":
        pv = norm_preview({"type": "page", **a})
        if pv:
            out.append({"what": "쪽 설정", "preview": pv})
    elif action == "hwp_replace":
        pv = norm_preview({"type": "change", "before": a.get("find"), "after": a.get("replace") if a.get("replace") is not None else a.get("to"),
                           "what": "바꾼 글"})
        if pv:
            out.append({"what": "바꾼 글", "preview": pv})
    return [x for x in out if x.get("preview")]


def from_written(written: dict) -> list[dict]:
    """서브 에이전트가 맡아 쓴 절(delegate_section) → 반영 미리 보기 — 글 앞부분과 그 안의 표. 걸음 인자에는 본문이 없어 Delegator.written 에서."""
    out: list[dict] = []
    for sec, text in (written or {}).items():
        body = _plain(str(text or ""))
        if body:
            pv = norm_preview({"type": "text", "text": body})
            if pv:
                out.append({"what": f"「{_s(sec, 40)}」 글", "preview": pv})
        tb = md_table(str(text or ""))
        if tb:
            out.append({"what": f"「{_s(sec, 40)}」 표", "preview": tb})
    return out


def from_steps(steps: list[dict], refused: tuple[str, ...] = ()) -> list[dict]:
    """한 요청의 걸음들 → 반영 미리 보기(같은 자리는 마지막 것만, 최대 DONE_MAX)."""
    got: dict[str, dict] = {}
    for s in steps or []:
        for x in from_step(str(s.get("action") or ""), s.get("args") or {}, str(s.get("observation") or ""), refused):
            got.pop(x["what"], None)
            got[x["what"]] = x
    return list(got.values())[-DONE_MAX:]
