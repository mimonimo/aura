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
VALUE_MAX = 2000                     # 값 하나의 길이 상한 — 여러 줄 값(명단·일정표·표 칸)을 담는다
_TAIL = re.compile(r"\s*(?:입니다|이다|이고|이며|임|예요|에요)\.?$")


def pending(db, session_id: int) -> list[dict]:
    try:
        return [a for a in json.loads(db.get_setting(f"chat_asks:{session_id}", "") or "[]") if a.get("name")]
    except Exception:
        return []


FACT, DECISION = "사실", "결정"   # 사실: 자료에 있을 수 있는 값(찾아본다) · 결정: 담당자가 정할 목표·계획(찾지 않고 묻는다)


def norm_kind(kind) -> str:
    k = str(kind or "")
    return DECISION if DECISION in k else FACT if FACT in k else ""


def set_kinds(db, session_id: int, kinds: dict) -> int:
    """갈래가 없는 받지 못한 값에 모델이 정한 갈래를 한 번 붙인다(이미 정한 갈래는 바꾸지 않는다). 붙인 수를 돌려준다."""
    cur, n = pending(db, session_id), 0
    for a in cur:
        k = norm_kind((kinds or {}).get(a["name"]))
        if k and not norm_kind(a.get("kind")):
            a["kind"], n = k, n + 1
    if n:
        set_pending(db, session_id, cur)
    return n


def ask_keys(name: str) -> set[str]:
    """값 이름의 낱말(두 글자 이상)과 그 두 글자 앞머리 — 검색어·결과 글이 이 값을 다루는지 볼 때 쓴다."""
    keys = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", name or "")}
    return keys | {w[:2] for w in keys if len(w) > 2}


def set_pending(db, session_id: int, asks: list[dict]) -> None:
    db.set_setting(f"chat_asks:{session_id}", json.dumps(asks, ensure_ascii=False) if asks else "")


def add_pending(db, session_id: int, asks: list[dict]) -> list[dict]:
    cur = pending(db, session_id)
    names = {a["name"] for a in cur}
    new = [a for a in asks if a.get("name") and a["name"] not in names]
    if new:                                   # 새로 물을 값이 생기면 앞서 미룬 값도 다시 차례를 다툰다(맨 뒤 자리는 그대로)
        for a in cur:
            a.pop(LATER, None)
    cur += new
    set_pending(db, session_id, cur)
    return cur


LATER = "later"


def defer(db, session_id: int, names) -> list[str]:
    """담당자가 「모름·나중에」 로 고른 값 — 이번 칸에서 빼고 차례의 맨 뒤로 보낸다. 미룬 값을 돌려준다.

    칸은 받을 값 목록의 끝(가장 최근)부터 채우므로 「맨 뒤 차례」 는 목록의 앞자리다. 미룬 값에는 표시를 붙여
    이번 칸에서 빼고(form_option), 다른 값이 다 차거나 담당자가 다시 미루면(표시를 새로 매긴다) 다음 차례에 나온다.
    물었던 이름만 받는다 — 엉뚱한 이름은 무시한다."""
    cur = pending(db, session_id)
    want = [n for n in dict.fromkeys(str(x).strip() for x in (names or [])) if n]
    have = {a["name"] for a in cur}
    want = [n for n in want if n in have]
    if not want:
        return []
    for a in cur:
        a.pop(LATER, None)
    later = [a for a in cur if a["name"] in want]
    for a in later:
        a[LATER] = True
    set_pending(db, session_id, later + [a for a in cur if a["name"] not in want])
    return [a["name"] for a in later]


def _facts_key(session_: dict | None) -> str:
    pid = (session_ or {}).get("project_id")
    return f"project_facts:{int(pid)}" if pid else f"chat_facts:{(session_ or {}).get('id', 0)}"


def _load(db, key: str) -> dict:
    try:
        got = json.loads(db.get_setting(key, "") or "{}")
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}


UNCONFIRMED = "확인 필요"


def suggestions(db, session_: dict | None) -> dict:
    """자료에서 찾았지만 담당자가 아직 확인하지 않은 값 {이름: {value, title, doc_id, chunk_id, run, at}}."""
    return _load(db, _facts_key(session_) + ":suggested")


def facts(db, session_: dict | None) -> dict:
    """담당자가 알려 준 값 + 기관 정보(설정·문서 인출). 자료에서 찾고 확인 전인 값은 「(확인 필요 …)」 표시를 붙여서만 싣는다 —
    초안에 들어가도 담당자 눈에 띄고, 수치는 여느 재료처럼 수치 근거 대조(number_evidence)를 거친다."""
    out = {k: v for k, v in institution.facts(db).items() if v}
    out.update({k: v for k, v in _load(db, _facts_key(session_)).items() if v})
    for k, s in suggestions(db, session_).items():
        if k not in out and s.get("value"):
            out[k] = f"{s['value']} ({UNCONFIRMED} · 자료에서 찾음 《{s.get('title', '')}》)"
    return out


def remember(db, session_: dict | None, given: dict, *, global_ok: bool = False) -> list[str]:
    """알려 준 값을 이 프로젝트(없으면 대화)의 값으로 기억한다. 기관 정보 이름(대학명·주소·총장·대표전화)은 기관 설정이
    모든 사용자에게 함께 쓰이므로 global_ok(관리자)일 때만 기관 설정에도 쓴다. 기관 설정에 쓰지 않은 기관 정보 이름을 돌려준다.
    담당자가 준 값은 같은 이름의 자료 제안(확인 전)을 대신한다 — 「확인」 은 이 길로 들어온다."""
    key = _facts_key(session_)
    cur = _load(db, key)
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
    sug = suggestions(db, session_)
    if any(k in sug for k in given):
        db.set_setting(key + ":suggested", json.dumps({k: v for k, v in sug.items() if k not in given}, ensure_ascii=False))
    return local_only


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def resolve_from_evidence(db, session_: dict | None, name: str, value: str, source_title: str,
                          hits: list[dict], *, allowed_docs: set[int], run: str = "") -> str:
    """받지 못한 값을 이번 검색 결과에서 찾아 「자료에서 찾음 · 확인 전」 제안으로 남긴다 — 담당자에게 묻기 전에 자료로 채우는 길.

    hits 는 이번 요청에서 search 가 모델에게 보인 결과 [{title, text, doc_id, chunk_id}]뿐이다. 받는 조건(모두):
    - name 이 물어 둔 값이고, 담당자가 이미 준 값이 없다(담당자 값은 덮어쓰지 않는다).
    - 결과가 이 프로젝트의 문서(allowed_docs — 프로젝트·기준 문서, 열람 범위 안)에서 왔다. 문서함 전체 결과로는 채우지 않는다.
    - value(공백 무시 두 글자 이상)가 source_title 과 같은 제목의 그 결과 글에 글자 그대로 있다.
    받은 값은 확인 전 제안으로만 둔다: 담당자 값(facts)에 바로 넣지 않고, 「필요한 값 입력」에 미리 채워 「확인」을 받는다.
    기관 설정에는 어떤 경우에도 쓰지 않는다. 감사용 출처(문서 id·조각 id·실행 id·시각)를 함께 남긴다.

    프로젝트 문서 속 「값은 X」 같은 주입 글은 X 가 그 글에 그대로 있을 때 제안까지만 될 수 있다 — 출처가 담당자에게 보이고
    확인 전에는 담당자 값이 되지 않으며 초안에도 「(확인 필요)」 표시와 함께만 들어간다.
    받으면 빈 글, 아니면 받지 않은 까닭을 돌려준다."""
    sid = int((session_ or {}).get("id") or 0)
    if name not in {a["name"] for a in pending(db, sid)}:
        return f"「{name}」 은(는) 물어 둔 값이 아니다 — 상태의 받지 못한 값 이름 그대로 쓴다."
    key = _facts_key(session_)
    if _load(db, key).get(name):
        return f"「{name}」 은(는) 담당자가 이미 알려 준 값이 있다 — 바꾸지 않는다."
    if name in suggestions(db, session_):
        return f"「{name}」 은(는) 이미 자료 제안이 있어 담당자 확인을 기다린다."
    want = _squash(value)
    if len(want) < 2:
        return "값이 비었거나 너무 짧다."
    title = _squash(source_title).strip("《》「」")
    same = [h for h in hits if _squash(h.get("title")).strip("《》「」") == title]
    if not same:
        return f"《{source_title[:40]}》 은(는) 이번 검색 결과의 출처 제목이 아니다 — 결과에 보인 《제목》 그대로 쓴다."
    own = [h for h in same if h.get("doc_id") is not None and int(h["doc_id"]) in allowed_docs]
    if not own:
        return "이 출처는 이 프로젝트의 문서(프로젝트·기준 문서)가 아니다 — 값은 프로젝트 문서의 검색 결과로만 채운다."
    hit = next((h for h in own if want in _squash(h.get("text"))), None)
    if hit is None:
        return "그 값이 이 출처의 검색 결과 글에 그대로 없다 — 결과에 적힌 글자 그대로만 기억한다."
    from datetime import datetime

    sug = suggestions(db, session_)
    sug[name] = {"value": value.strip()[:VALUE_MAX], "title": str(hit.get("title") or "")[:120], "doc_id": hit.get("doc_id"),
                 "chunk_id": hit.get("chunk_id"), "run": run, "at": datetime.now().isoformat(timespec="seconds")}
    db.set_setting(key + ":suggested", json.dumps(sug, ensure_ascii=False))
    return ""


_DATEISH = re.compile(r"(?:'?\d{2,4}\s*[.년/-]\s*)?\d{1,2}\s*[.월/-]\s*\d{1,2}\s*\.?(?:일)?(?:\s*\([^)]{1,3}\))?"
                      r"(?:\s*[~∼-]\s*(?:'?\d{2,4}\s*[.년/-]\s*(?=\d{1,2}\s*[.월/-]\s*\d))?(?:\d{1,2}\s*[.월/-]\s*)?\d{1,2}\s*\.?(?:일)?(?:\s*\([^)]{1,3}\))?)?"
                      r"(?:\s*,?\s*\d{1,2}:\d{2}(?::\d{2})?)?"
                      r"|\d[\d,.]*\s*(?:억|만|천)?\s*(?:원|명|개|건|%|시간|일|개월|년)")


def _mark(line: str) -> str:
    """목록 줄의 글머리 꼴 — 번호(1. 2))·원 번호·표 줄·글머리 기호 하나."""
    t = line.lstrip()
    if re.match(r"\d", t):
        return "번호"
    if re.match(r"[①-⑳]", t):
        return "원번호"
    return t[:1]


def candidate_spans(name: str, hits: list[dict], source_title: str, limit: int = 4) -> list[str]:
    """값이 결과 글에 그대로 없을 때 고쳐 낼 후보 — 그 출처 결과 글에서 그대로 떼어 낸 조각만(바꿔 쓰지 않는다).
    물은 이름의 낱말(두 글자 앞머리 포함)이 든 줄과, 날짜·기간·수량 꼴 글, 그 줄 뒤에 이어진 목록·표 줄 묶음(여러 줄 값)을 고른다.
    모델은 이 가운데 하나를 그대로 다시 낸다."""
    title = _squash(source_title).strip("《》「」")
    texts = [str(h.get("text") or "") for h in hits if _squash(h.get("title")).strip("《》「」") == title]
    keys = ask_keys(name)
    ranked: list[tuple[int, int, list[str]]] = []
    for ti, text in enumerate(texts):
        lines = [x.strip() for x in re.split(r"[\n。]|(?<=[.)])\s{2,}", text) if x.strip()]
        for i, line in enumerate(lines):
            score = sum(1 for k in keys if k in line)
            if not score:
                continue
            got = [m.group(0).strip() for m in _DATEISH.finditer(line)]
            # 이름 줄 뒤에 목록·표 줄이 이어지면 그 묶음(여러 줄 값 — 명단·일정표)도 후보로. 글은 결과에 있던 줄 그대로 잇는다
            run: list[str] = []
            for nxt in lines[i + 1:i + 13]:
                if not _ITEM_LINE.match(nxt) or (run and _mark(nxt) != _mark(run[0])):   # 글머리 꼴이 바뀌면(「4. 다음 절」) 끝
                    break
                run.append(nxt)
            if len(run) >= 2:
                got.append("\n".join(run)[:VALUE_MAX])
            if len(_squash(line)) < 30 and i + 1 < len(lines):      # 짧은 줄은 제목·항목 이름일 때가 많다 — 값은 다음 줄에
                got.append(lines[i + 1][:80])
            got.append(line[:80])
            ranked.append((-score, ti * 1000 + i, got))
    out = [c for _s, _i, got in sorted(ranked, key=lambda x: (x[0], x[1])) for c in got]   # 이름 낱말이 많이 든 줄부터
    seen: list[str] = []
    for c in out:
        if len(_squash(c)) >= 2 and c not in seen:
            seen.append(c)
    return seen[:limit]


def form_option(asks: list[dict], suggested: dict | None = None) -> dict:
    """선택지 규격 kind=form — 화면은 fields 를 입력 칸으로 그리고, 채운 값을 '이름: 값; …' 문장으로 보낸다.
    자료에서 찾은 확인 전 값(suggested)은 칸에 미리 채우고 출처를 알린다 — 담당자가 고치거나 그대로 「확인」."""
    suggested = suggested or {}
    fields = []
    # 칸은 여섯 개까지 — 가장 최근에 모은 값부터(방금 답이 물은 값이 칸에 있어야 답과 칸이 맞는다). 앞 절에서 모은 나머지는
    # 이 칸을 채우거나 기억하면 다음 차례에 나온다. 「나중에」 로 미룬 값(defer)은 다른 값이 남아 있는 동안 칸에서 뺀다
    now = [a for a in asks if not a.get(LATER)] or asks
    shown = now[-6:]
    for a in shown:
        s = suggested.get(a["name"]) or {}
        found = bool(s.get("value"))
        hint = (f"자료에서 찾음 · 확인 전 — 《{s.get('title', '')}》" if found else a.get("hint") or "")
        fields.append({"name": a["name"], "label": a["name"], "placeholder": hint, "required": False, "value": s.get("value") or "",
                       "hint": a.get("hint") or "", "source": str(s.get("title") or "") if found else "", "suggested": found})
    return {"kind": "form", "text": "필요한 값 입력", "fields": fields, "more": len(asks) - len(shown),
            "later": sum(1 for a in asks if a.get(LATER) and a not in shown),
            "template": "; ".join(f"{a['name']}: {{{a['name']}}}" for a in shown),
            "question": "; ".join(f"{a['name']}: (값)" for a in shown),
            "submit": "확인" if any(f["value"] for f in fields) else "저장"}

# 여러 줄 값의 이어지는 줄 — 목록 글머리(-·•·1.·①)나 표 줄(|)로 시작하는 줄
_ITEM_LINE = re.compile(r"^\s*(?:[-•·∙*○●□■▪]|\d{1,2}[.)]|[①-⑳]|\|)")


def parse_pairs(text: str, names: list[str]) -> dict:
    """"사업단명: AI-X 사업단; 총괄책임자 성명은 홍길동" → {이름: 값}. 물었던 이름만 받는다(엉뚱한 문장을 값으로 오해하지 않게).

    여러 줄 값(표 칸·목록): 「참여 기관:」 처럼 이름 뒤가 비었으면 다음 줄들이 값이다(다음 이름 줄·빈 줄까지). 값이 있는 이름 뒤에는
    목록 글머리·표 줄로 시작하는 줄만 이어 붙인다 — 「그리고 초안 써 줘」 같은 다음 문장은 값에 섞지 않는다."""
    out: dict = {}
    if not text or not names:
        return out
    order = sorted(names, key=len, reverse=True)

    def match(piece: str):
        for name in order:
            m = re.match(rf"\s*{re.escape(name)}\s*(?:[:：]|은|는|이|가)\s*(.*)$", piece)
            if m:
                return name, m.group(1).strip()
        return None

    def keep(name: str, raw: str) -> None:
        val = _TAIL.sub("", raw.strip()).strip(" .") if "\n" not in raw else raw.strip()
        if val and not re.fullmatch(r"[○◯?？]+|\(값\)|\(번호\)", val):
            out[name] = val[:VALUE_MAX]

    block: str | None = None          # 지금 여러 줄 값을 모으는 이름
    block_open = False                # 이름 뒤가 비어 다음 줄들이 값인가
    lines: list[str] = []

    def close() -> None:
        nonlocal block, lines
        if block is not None and lines:
            keep(block, "\n".join(lines))
        block, lines = None, []

    for line in text.split("\n"):
        if not line.strip():                             # 빈 줄은 여러 줄 값의 끝
            close()
            continue
        if block is not None:
            if match(line) is None and (not lines or _ITEM_LINE.match(line) or block_open):
                lines.append(line.rstrip())
                continue
            close()
        pieces = [p for p in _SEP.split(line) if p and p.strip()]
        for i, piece in enumerate(pieces):
            got = match(piece)
            if got is None:
                continue
            name, raw = got
            if i == len(pieces) - 1:                     # 줄의 마지막 값 — 다음 줄이 이어질 수 있다
                block, block_open = name, not raw
                lines = [raw] if raw else []
                if raw:
                    keep(name, raw)
            else:
                keep(name, raw)
    close()
    return out


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

