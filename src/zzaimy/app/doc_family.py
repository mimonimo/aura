"""같은 제목의 기준 문서를 판본으로 묶고, 내용이 같은 문서를 가려낸다.

왜 필요한가. 공개 공고를 해마다 모으면 같은 양식이 여러 번 들어온다(실측 2026-09-22: 「첨단분야
혁신융합대학 사업 가 신청서」가 세 판, BRIDGE3.0 양식이 두 판). 파일 바이트가 같은 것은 반입 때
해시로 막지만, 해마다 조금씩 손본 양식은 해시가 다르다. 그대로 두면 기준 문서 목록에 같은 제목이
줄지어 서고, 검색은 같은 조각을 판본 수만큼 올린다.

어떻게 하나. 제목을 정규화한 값(family)으로 묶는다. 붙임 번호·괄호·공백·확장자는 제목이 아니다.
같은 묶음 안에서 본문이 거의 같으면(글자 조각 겹침 ≥ NEAR_DUP) 같은 내용으로 보고 뒤에 온 것을
받지 않는다. 본문이 다르면 판본으로 남기되 먼저 들어온 문서(version_of)에 잇는다.
붙임 번호 앞머리 모양이 같고 사업 이름이 같은 문서는 같은 공고에 딸린 것으로 보아 한 묶음(batch)이다.
"""

from __future__ import annotations

import re

NEAR_DUP = 0.95        # 이 이상 겹치면 같은 내용 — 판본이 아니라 중복
VERSION_MIN = 0.30     # 이보다 덜 겹치면 제목만 같은 다른 문서일 수 있다 — 판본 표시만 하고 잇지 않는다

_EXT = re.compile(r"\.(hwpx?|pdf|docx?|xlsx?|pptx?|jpe?g|png|txt|md)$", re.I)
# 붙임 번호 앞머리 — (붙임2) · 붙임 2. · [붙임3] · 별첨1 · 별지 제1호 서식
_ATTACH = re.compile(
    r"^\s*[\[(（【]?\s*(?:붙임|별첨|별지|첨부)\s*(?:제)?\s*(\d+)?\s*(?:호)?\s*(?:서식)?\s*[\])）】]?\s*[.:·]?\s*")
_YEAR = re.compile(r"(20\d{2})\s*(?:년|\.)")
_NOISE = re.compile(r"[\s_\-–—·ㆍ.,()\[\]{}（）「」『』【】<>《》〈〉'\"“”‘’:;!?/\\~]+")
# 양식·공고 같은 서류 종류 낱말 — 사업 이름을 뽑을 때 뗀다
_KIND_WORDS = re.compile(
    r"(신청서|확약서|동의서|정의서|산출근거|사업계획서|계획서|추진계획|기본계획|공고문|공고|양식|서식|"
    r"작성요령|작성안내|안내|매뉴얼|지침|가이드|결과보고서|보고서|평가|심사|기준)")


def family_key(filename: str) -> str:
    """제목을 판본 묶음 열쇠로 — 붙임 번호·괄호·공백·확장자를 뗀 소문자."""
    s = _EXT.sub("", (filename or "").strip())
    s = _ATTACH.sub("", s)
    s = _NOISE.sub("", s)
    return s.lower()


def attachment_no(filename: str) -> int | None:
    m = _ATTACH.match((filename or "").strip())
    if m and m.group(1):
        return int(m.group(1))
    return None


def batch_style(filename: str) -> str:
    """붙임 앞머리의 생김새 — '(붙임#)' · '붙임 #.' · '붙임#.' 처럼 번호만 #으로 바꾼 것. 없으면 빈 문자열."""
    m = _ATTACH.match((filename or "").strip())
    if not m or not m.group(1):
        return ""
    head = m.group(0)
    return re.sub(r"\d+", "#", head).strip()


PROGRAM_CHARS = 8      # 사업 이름 열쇠로 쓰는 앞글자 수 — 붙임마다 뒤쪽 낱말(가/본/정의서)이 달라도 앞머리는 같다


def program_key(filename: str) -> str:
    """제목에서 붙임 번호·연도·서류 종류 낱말을 뗀 앞머리 — 같은 공고에 딸린 붙임들은 이 값이 같다."""
    s = _EXT.sub("", (filename or "").strip())
    s = _ATTACH.sub("", s)
    s = _YEAR.sub("", s)
    s = _KIND_WORDS.sub("", s)
    s = _NOISE.sub("", s)
    return s.lower()[:PROGRAM_CHARS]


def batch_key(filename: str) -> str:
    """같은 공고에 딸린 붙임 묶음의 열쇠 — 앞머리 모양 + 사업 이름. 붙임이 아니면 빈 문자열."""
    style = batch_style(filename)
    return f"{style}|{program_key(filename)}" if style else ""


def year_of(text: str, filename: str = "") -> str:
    """문서의 연도 — 제목, 없으면 본문 앞머리에서 '20NN년'. 없으면 빈 문자열."""
    for src in (filename or "", (text or "")[:600]):
        m = _YEAR.search(src)
        if m:
            return m.group(1)
    return ""


def _shingles(text: str, n: int = 8, step: int = 3) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    if len(t) <= n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(0, len(t) - n + 1, step)}


def similarity(a: str, b: str) -> float:
    """두 본문의 글자 조각(8자, 공백 제거) 겹침 비율 — 0~1. 서식 차이(띄어쓰기·괄호)에 무디다."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def link_attachments(db, doc_id: int, doc_type: str = "regulation") -> int | None:
    """같은 공고에 딸린 붙임 묶음에서 머리 문서를 찾아 이 문서를 잇는다(related_criteria_id).

    머리 문서는 묶음에서 갈래가 공고·계획서인 것, 없으면 붙임 번호가 가장 작은 것이다.
    이 문서가 머리면 아무것도 하지 않는다. 돌려주는 것은 이은 머리 문서 id.
    """
    doc = db.get_document(doc_id)
    if not doc:
        return None
    key = batch_key(doc.get("filename") or "")
    if not key:
        return None
    mates = [d for d in db.list_documents(doc_type)
             if d["id"] != doc_id and d.get("status") != "failed"
             and batch_key(d.get("filename") or "") == key]
    if not mates:
        return None
    pool = mates + [doc]

    def rank(d: dict) -> tuple:
        head_kind = 0 if (d.get("kind") in ("announcement", "plan")) else 1
        return (head_kind, attachment_no(d.get("filename") or "") or 99, d["id"])

    head = min(pool, key=rank)
    if head["id"] == doc_id:
        return None
    db.set_document_family(doc_id, doc.get("family"), related_criteria_id=head["id"])
    return head["id"]


def judge(text: str, siblings: list[dict]) -> dict:
    """같은 묶음의 기존 문서들과 견줘 판정한다.

    돌려주는 것: {"duplicate_of": id|None, "version_of": id|None, "score": 최대 겹침}.
    duplicate_of 가 있으면 받지 않는다. version_of 는 묶음의 첫 문서(가장 오래된 것)다.
    """
    best_id, best = None, 0.0
    for s in siblings:
        sc = similarity(text, s.get("masked_text") or "")
        if sc > best:
            best_id, best = s["id"], sc
    if best_id is not None and best >= NEAR_DUP:
        return {"duplicate_of": best_id, "version_of": None, "score": best}
    head = min((s["id"] for s in siblings), default=None)
    return {"duplicate_of": None, "version_of": head, "score": best}
