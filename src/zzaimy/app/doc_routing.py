"""문서가 들어올 때 갈래와 보관 위치를 스스로 정한다.

왜 필요한가. 담당자가 문서마다 갈래를 고르면 수백 건에서 성립하지 않는다.
실제로 운영 자료 112건 가운데 107건이 같은 갈래로 몰려 있었다. 반입할 때
고정값을 넣었기 때문이고, 그 상태에서는 갈래로 무엇도 가를 수 없다.

어떻게 정하는가. 두 가지를 쓴다.

1. **갈래**는 문서의 생김새로 본다. 조문 머리(제N조)가 줄지어 있으면 규정이고,
   신청 기간·지원 규모 같은 공고의 뼈대가 있으면 공고다. 글자층이 없어 판독을
   거친 것은 추출 문서다. 어느 쪽도 아니면 일반 행정으로 둔다.
2. **영역**은 이미 분류된 문서에서 배운다. 낱말을 손으로 나열하지 않고, 지금
   저장소에 있는 문서의 이름과 본문을 보고 어떤 말이 어느 영역에 치우치는지
   센다. 새 문서가 들어오면 그 치우침으로 고른다. 자료가 늘면 판단도 같이 넓어진다.
   이름만 보면 `law01.pdf` 처럼 뜻 없는 이름에서 틀린다. 그래서 본문도 함께 본다.

확신이 없으면 고르지 않는다. 억지로 붙인 갈래는 없느니만 못하다.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

_WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_EXT_RE = re.compile(r"\.(hwpx?|pdf|docx?|xlsx?|pptx?|jpe?g|png|txt|md)$", re.I)
_ARTICLE = re.compile(r"제\s*\d+\s*조")

# 경계값 — 낮출수록 더 자주 고른다. 근거는 주석에 남긴다.
ARTICLE_MIN = 3          # 조문 머리가 이만큼 나오면 규정으로 본다
SECTOR_MIN_DOCS = 8      # 이보다 적게 분류돼 있으면 배울 것이 없다
SECTOR_MARGIN = 1.25     # 으뜸이 버금보다 이만큼 앞서야 고른다


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text or ""))


BODY_CHARS = 1500        # 본문에서 볼 앞부분 — 앞머리에 문서 성격이 드러난다


def _doc_words(db, doc: dict) -> set[str]:
    """이름과 본문 앞부분의 낱말. 이름이 뜻 없어도 본문으로 가려낸다."""
    words = _terms(doc.get("filename") or "")
    body: list[str] = []
    total = 0
    for c in db.list_doc_chunks(doc["id"]):
        t = (c.get("content") or "").strip()
        if not t or t.startswith("{"):        # 표는 JSON 이라 낱말로 쓰지 않는다
            continue
        body.append(t)
        total += len(t)
        if total >= BODY_CHARS:
            break
    return words | _terms(" ".join(body)[:BODY_CHARS])


def learn_sectors(db) -> dict[str, dict[str, float]]:
    """이미 분류된 문서에서 낱말별 영역 치우침을 배운다.

    한 영역에만 나오는 말일수록 크게 친다. 모든 영역에 나오는 말은 신호가 아니다.
    """
    by_sector: dict[str, Counter] = defaultdict(Counter)
    doc_freq: Counter = Counter()
    n_docs = 0
    for d in db.list_documents():
        sector = d.get("sector") or ""
        if not sector:
            continue
        words = _doc_words(db, d)
        if not words:
            continue
        n_docs += 1
        by_sector[sector].update(words)
        doc_freq.update(words)
    if n_docs < SECTOR_MIN_DOCS:
        return {}
    weights: dict[str, dict[str, float]] = {}
    for sector, counts in by_sector.items():
        total = sum(counts.values()) or 1
        w: dict[str, float] = {}
        for word, n in counts.items():
            idf = math.log(1 + n_docs / max(doc_freq[word], 1))
            w[word] = (n / total) * idf
        weights[sector] = w
    return weights


def guess_sector(db, filename: str, weights: dict | None = None,
                 text: str = "") -> tuple[str, str]:
    """이름과 본문을 보고 영역을 고른다. 확신이 없으면 ("", 사유)."""
    weights = learn_sectors(db) if weights is None else weights
    if not weights:
        return "", "분류된 문서가 적어 배울 것이 없습니다"
    words = _terms(filename) | _terms((text or "")[:BODY_CHARS])
    if not words:
        return "", "이름과 본문에서 쓸 말을 찾지 못했습니다"
    scored = sorted(
        ((sum(w.get(x, 0.0) for x in words), sector) for sector, w in weights.items()),
        reverse=True,
    )
    if not scored or scored[0][0] <= 0:
        return "", "어느 영역과도 겹치지 않습니다"
    best, runner = scored[0], (scored[1] if len(scored) > 1 else (0.0, ""))
    if runner[0] > 0 and best[0] < runner[0] * SECTOR_MARGIN:
        return "", f"「{best[1]}」과 「{runner[1]}」이 비슷해 정하지 못했습니다"
    return best[1], f"이름이 「{best[1]}」 문서들과 닮았습니다"


def guess_doc_type(text: str, identity: dict | None = None,
                   scanned: bool = False) -> tuple[str, str]:
    """생김새로 갈래를 고른다. 돌려주는 것은 (갈래, 이유)."""
    body = text or ""
    if scanned:
        return "ocr", "글자층이 없어 판독을 거친 문서입니다"
    articles = len(_ARTICLE.findall(body))
    if articles >= ARTICLE_MIN:
        return "regulation", f"조문 머리가 {articles}번 나옵니다"
    ident = identity or {}
    marks = [k for k in ("period", "scale", "target") if (ident.get(k) or "").strip()]
    if (ident.get("program") or "").strip() and len(marks) >= 2:
        return "grant", "사업 이름과 신청 조건이 함께 있습니다"
    return "auto", "규정이나 공고의 뼈대가 보이지 않습니다"


# 서류 갈래 — 처리 경로(doc_type)와 별개로 문서가 어떤 서류인지. 화면 표시와 연관(공고↔양식↔계획서)에 쓴다.
KINDS = {
    "announcement": "공고", "form": "양식", "plan": "계획서", "report": "결과보고서",
    "regulation": "규정", "guideline": "지침·매뉴얼", "criteria": "심사·평가 기준", "notice": "안내문",
}
# 제목·본문의 낱말 단서 — 특정 문서가 아니라 서류 종류에 붙는 일반 낱말이다(하드코딩 금지 규칙과 충돌하지 않는다)
_KIND_CUES = {
    "regulation": r"학칙|규정|규칙|세칙|규약|내규|정관|조례",
    "form": r"신청서|확약서|동의서|서약서|정의서|양식|서식|신고서|원서|위임서|제출서|이력서|추천서|"
            r"(?:복학|휴학|자퇴|퇴학|전과|입학|편입|재입학|사직|휴직|복직|출원)원$",
    "announcement": r"공고|모집|공모",
    "plan": r"계획서|추진\s*계획|기본\s*계획|시행\s*계획|운영\s*계획|사업\s*계획",
    "report": r"결과보고|성과보고|실적보고|결과 보고|최종보고|보고서$|보고$",
    "guideline": r"지침|매뉴얼|안내서|가이드|요령|편람|길라잡이|처리기준|업무기준|운영기준|처리 기준",
    "criteria": r"심사기준|평가기준|평가지표|배점|채점|심사표|평가표",
    "notice": r"안내|공지|알림",
}
_KIND_ORDER = ("regulation", "form", "report", "criteria", "announcement", "guideline", "plan", "notice")
# 양식의 몸 — 빈칸 표시가 잦다: '년 월 일', '○○', 'OOO', '☐', '□', '( )'
_BLANK = re.compile(r"년\s*월\s*일|[○◯]{2,}|(?<![A-Za-z])[oO]{3,}(?![A-Za-z])|[☐□■]|\(\s{2,}\)")
_APPLY = re.compile(r"신청\s*(?:기간|기한|접수)|접수\s*(?:기간|기한)|공고합니다|공고함")


def guess_kind(filename: str, text: str, doc_type: str | None = None) -> tuple[str, str]:
    """서류 갈래를 고른다 — (갈래, 이유). 확신이 없으면 ('', 이유).

    제목의 낱말이 먼저다(사람도 제목으로 서류를 안다). 제목에 단서가 없으면 본문 앞머리의
    생김새로 본다: 조문 머리가 줄지어 있으면 규정, 빈칸 표시가 잦으면 양식, 공고 뼈대면 공고.
    """
    title = _EXT_RE.sub("", filename or "")
    for kind in _KIND_ORDER:
        if re.search(_KIND_CUES[kind], title):
            return kind, f"제목에 「{KINDS[kind]}」 낱말이 있습니다"
    body = (text or "")[:BODY_CHARS * 2]
    if doc_type == "regulation" and len(_ARTICLE.findall(body)) >= ARTICLE_MIN:
        return "regulation", "조문 머리가 줄지어 있습니다"
    if len(_ARTICLE.findall(body)) >= ARTICLE_MIN * 2:
        return "regulation", "조문 머리가 줄지어 있습니다"
    blanks = len(_BLANK.findall(body))
    if body and blanks * 1000 / max(len(body), 1) >= 3:
        return "form", "빈칸 표시(년 월 일·○○·☐)가 잦습니다"
    head = body[:800]
    if _APPLY.search(body) and re.search(r"공고", head):
        return "announcement", "앞머리가 공고이고 신청 기간이 있습니다"
    for kind in ("report", "criteria", "plan", "guideline"):
        if re.search(_KIND_CUES[kind], head):
            return kind, f"앞머리에 「{KINDS[kind]}」 낱말이 있습니다"
    return "", "서류 갈래를 정할 단서가 없습니다"


def route(db, filename: str, text: str, identity: dict | None = None,
          scanned: bool = False) -> dict:
    """반입되는 문서 한 건의 갈래와 영역을 정한다.

    고르지 못한 항목은 빈 문자열로 두고 사유를 함께 돌려준다. 호출부가 기존 값을
    유지할지 담당자에게 물을지 정한다.
    """
    doc_type, why_type = guess_doc_type(text, identity, scanned)
    sector, why_sector = guess_sector(db, filename, text=text)
    kind, why_kind = guess_kind(filename, text, doc_type)
    return {
        "doc_type": doc_type, "why_type": why_type,
        "sector": sector, "why_sector": why_sector,
        "kind": kind, "why_kind": why_kind,
    }
