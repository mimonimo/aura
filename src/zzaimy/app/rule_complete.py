"""답이 인용한 규정 조항의 시점·단서를 빠뜨렸는지 — 답 뒤 관문(한 번 보충).

왜: 「교원의 정년은 만 65세로 하고, 정년으로 인한 퇴직의 시기는 정년이 되는 날이 속하는 학기의 말일로 한다」 를 근거로 받고도
답에는 「정년은 만 65세」 만 남아 퇴직일 계산이 틀렸다(11-10 대화 183). 조각은 온전했고 답 쓰기에서 뒷부분을 줄였다.

어떻게: 모델에 건넨 근거 글에서 조항(《제목 · 제N조(…)》 머리 + 본문)을 읽고, 답이 그 조항을 짚었으면(제N조 + 제목 낱말)
그 조항 본문의 「시점 말」(말일·초일·익일·기산·속하는 … 같은 일반 낱말)과 단서절(다만·하되·단,) 안의 수치를 답이 담았는지 본다.
특정 규정·조항의 문구는 코드에 두지 않는다 — 낱말 갈래(시점·단서 표지·수치+단위)만 쓴다.
"""
from __future__ import annotations

import re

# 시점·기한을 정하는 일반 낱말 — 빠지면 날짜 계산이 달라진다
_TIME = re.compile(r"(말일|초일|익일|전일|만료일|기산|속하는\s*(?:달|월|학기|학년도|연도|해)|다음\s*날|다음\s*달|해당\s*(?:학기|월|연도))")
# 단서·예외 표지 — 그 뒤 절의 수치·시점이 빠지면 조건이 사라진다
_PROVISO = re.compile(r"(다만[,，]?|하되[,，]?|단[,，]\s|불구하고)")
_NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:세|년|개월|월|일|주|시간|학기|학년|학점|회|명|퍼센트|%)")
# 조항 머리 — 《규정 이름 · 제5조(교원정년)》 꼴(responder._cite·에이전트 근거 머리)
_HEAD = re.compile(r"《([^》\n]{2,120}?)(?:\s*·\s*([^》\n]{0,80}))?》([^\n]*)\n")
_ART = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


def _squash(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _art_key(t: str) -> str | None:
    m = _ART.search(t or "")
    if not m:
        return None
    return f"제{m[1]}조" + (f"의{m[2]}" if m[2] else "")


def articles(context: str) -> list[dict]:
    """근거 글 → [{title, key(제N조), text}] — 머리에 조항 번호가 있거나 본문 첫머리가 조항인 덩어리만."""
    out = []
    heads = list(_HEAD.finditer(context or ""))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(context)
        body = context[m.end():end].strip()
        body = body.split("\n[", 1)[0]                          # 다음 묶음 머리([사업 문서 …])에서 끊는다
        key = _art_key(m[2] or "") or _art_key(m[3] or "") or _art_key(body[:30])
        if not key or not body:
            continue
        title = re.sub(r"\s*·.*$", "", m[1]).strip()
        out.append({"title": title, "key": key, "text": body[:2000]})
    return out


def _title_words(title: str) -> list[str]:
    """제목의 알맹이 낱말 — 기관 이름·「규정」 같은 흔한 꼬리를 뺀 두 글자 넘는 낱말."""
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", re.sub(r"\(.*?\)|\d{4}[.\-년]\s*\d{1,2}.*$", "", title))
    common = {"규정", "규칙", "내규", "지침", "세칙", "학칙", "운영", "관한", "대한"}
    return [w for w in words if w not in common and not re.fullmatch(r"\d+", w) and not w.endswith(("대학교", "대학"))] or words


def cited(answer: str, art: dict) -> bool:
    """답이 이 조항을 짚었나 — 조항 번호가 있고, 그 근처(같은 줄)나 답 어딘가에 제목 낱말이 있으면."""
    sq = _squash(answer)
    if art["key"] not in sq:
        return False
    words = _title_words(art["title"])
    for line in answer.splitlines():
        if art["key"] in _squash(line) and any(w in line for w in words):
            return True
    return False


def needed_terms(text: str) -> list[str]:
    """조항 본문에서 답에 있어야 할 말 — 시점 낱말 전부 + 단서절 안의 수치·단위."""
    terms = [_squash(m[0]) for m in _TIME.finditer(text)]
    for m in _PROVISO.finditer(text):
        clause = re.split(r"[.。]\s|\n", text[m.end():], maxsplit=1)[0]
        terms += [_squash(x) for x in _NUM_UNIT.findall(clause)]
    return list(dict.fromkeys(t for t in terms if t))


def _present(term: str, answer_sq: str) -> bool:
    if term in answer_sq:
        return True
    if term.startswith("속하는"):                              # 「속하는 학기」 → 답이 「해당 학기」·「그 학기」로 바꿔 써도
        return term[3:] in answer_sq
    return False


def missing(answer: str, context: str) -> list[dict]:
    """답이 짚은 조항 가운데 시점·단서 말이 빠진 것 — [{title, key, text, terms}]."""
    sq = _squash(answer)
    out, seen = [], set()
    for art in articles(context):
        k = (art["title"], art["key"])
        if k in seen or not cited(answer, art):
            continue
        seen.add(k)
        lack = [t for t in needed_terms(art["text"]) if not _present(t, sq)]
        if lack:
            out.append({**art, "terms": lack})
    return out


def supplement_prompt(gaps: list[dict]) -> str:
    """보충 요청 글 — 빠진 조항 원문과 빠진 말을 보이고 답 전체를 다시 쓰게."""
    lines = ["[보충] 답에 인용한 조항의 시점·단서가 빠졌다. 아래 조항 원문의 해당 대목을 그대로 반영해 답 전체를 다시 쓴다"
             " (계산이 있으면 이 조건을 넣어 다시 셈한다). 앞의 답과 같은 꼴을 지키고, 원문에 없는 말은 더하지 않는다."]
    for g in gaps[:3]:
        lines.append(f"《{g['title']}》 {g['key']} 원문: {g['text'][:600]}\n빠진 말: {', '.join(g['terms'][:6])}")
    return "\n\n".join(lines)


def quote_lines(gaps: list[dict]) -> str:
    """보충 뒤에도 빠졌으면 답 끝에 붙이는 조문 원문 — 담당자가 원문으로 확인하게."""
    return "\n\n※ 근거 조문 원문:\n" + "\n".join(f"- 《{g['title']}》 {g['key']}: {g['text'][:300].strip()}" for g in gaps[:2])
