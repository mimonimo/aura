"""문서의 정식 이름과 기준 날짜 — 첫 쪽 머리에서 결정론적으로 읽는다.

왜 필요한가. 규정집에서 받은 파일은 'law03.pdf'처럼 이름에 뜻이 없다. 이 이름이
검색 근거·인용(reg_title)에 그대로 실리면 담당자도 모델도 무슨 규정인지 모른다.
규정 문서는 첫 쪽 머리에 '영남이공대학교 산학협력단 사무분장 규정'과
'학과장회 통과일자 : 2022년 05월 26일'처럼 이름과 날짜를 적는 관행이 있으므로
그 줄을 찾는다. 모델을 쓰지 않고, 본문에 적힌 표현을 그대로 옮긴다.

찾지 못하면 None 을 돌려주고 호출부는 파일 이름을 그대로 쓴다.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# 규범 문서의 이름은 이 낱말로 끝난다 — 특정 문서가 아니라 문서 종류의 표지다
_TITLE_TAIL = ("규정", "규칙", "내규", "정관", "세칙", "지침", "요령", "기준", "강령",
               "시행령", "시행규칙", "학칙", "헌장", "매뉴얼", "편람")
_TITLE_LINE = re.compile(
    r"^[\s\d\-–.]*([가-힣A-Za-z0-9()「」·ㆍ ,]{2,60}?(?:" + "|".join(_TITLE_TAIL) + r"))\s*$"
)
# 조문·장 표기나 목적 문장은 이름이 아니다
_NOT_TITLE = re.compile(r"^\s*(?:제\s*\d+\s*[장조절관]|부\s*칙|\d+\.|[①-⑳])")
_DATE_LINE = re.compile(
    r"(?:통과|제정|개정|시행|공포|의결)\s*일\s*자?\s*[:：]?\s*"
    r"((?:19|20)\d{2}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}\s*일?)"
)
_HEAD_LINES = 12


# 느슨한 제목 — 규범 문서가 아닌 공지·안내문용. 파일 이름에 한글이 전혀 없을 때만 쓴다
_LOOSE_SKIP = re.compile(
    r"^(?:[\d\-–.()\s/]+$|제\s*\d+\s*[장조절]|\d+[.)]|[□○◦▪■●※▶-]|페이지|page"
    r"|\d{1,3}(?![년월일차기회주])(?=[가-힣])"       # '9납입금' 처럼 표의 번호 칸
    r"|[가-하][.)]\s"                                # '가. 신청기간' 처럼 항목 기호
    r"|[:：]"                                        # ': 대구광역시 …' 처럼 항목의 값만 남은 줄
    r"|[가-힣A-Za-z]{1,8}\s*[:：]"                   # '지원범위: 2026년 …' 처럼 항목:값 줄
    r"|\d{1,2}\s+(?=[가-힣])"                         # '1 모집분야 및 지원자격' 처럼 번호 붙은 절 제목
    r"|【"
    r"|\[\[)",                                       # 판독기의 속성 줄 '[[속성]] …'
    re.IGNORECASE)
# 공고·고시는 첫 줄에 문서 번호를 적고 그다음 줄에 제목을 적는다 — 번호 줄은 이름이 아니다
_DOC_NUMBER = re.compile(
    r"^[가-힣\s]*(?:공고|고시|공지|훈령|지침|규칙)\s*(?:제\s*)?\d{2,4}\s*-\s*\d+\s*호")


# 표의 칸 구분자 — 서식 문서는 첫 쪽이 표로 시작한다
_CELL = re.compile(r"[|｜￨┃│\t]")
# 표 칸에서만 쓰는 조건 — 칸은 대개 항목 이름('학년'·'비고')이라 문서 종류로 끝날 때만 이름으로 본다.
# 줄에는 걸지 않는다: 문서 종류 낱말은 목록으로 다 담을 수 없다('학생회칙'을 버렸다, 2026-09-21).
_KIND_TAIL = _TITLE_TAIL + ("서", "표", "원", "록", "집", "부", "안", "문", "황", "고", "장",
                            "증", "안내", "계획", "명세", "현황", "일정", "결과", "양식", "서식")
# 제목은 첫머리에 있다. 창을 넓히면 본문 중간 줄이 제목으로 잡힌다(2026-09-21 실측:
# 40줄로 넓혔더니 47건 중 상당수가 본문 문장으로 바뀌었다) — 규정과 같은 12줄로 둔다.
_LOOSE_LINES = _HEAD_LINES


def _kind_like(s: str) -> bool:
    return s.endswith(_KIND_TAIL)


def _clean_cell(c: str) -> str:
    """표 칸의 군더더기를 뗀다 — 빈 칸 표시('[]'·'()')와 칸 전체를 감싼 괄호."""
    c = re.sub(r"\[\s*\]|\(\s*\)", "", c).strip()
    while len(c) > 2 and (c[0], c[-1]) in ((("("), (")")), (("["), ("]"))):
        c = c[1:-1].strip()
    return _unspace(c)


def _cell_title(ln: str) -> str | None:
    """표 한 줄에서 문서 이름이 될 칸을 고른다.

    '영문성명등록신청서 | | 전결 | |' 처럼 서식 이름이 첫 칸에 오면 그 칸이 문서 이름이다.
    '|학년|학년|학번|' 처럼 항목 이름만 늘어선 줄에는 이름이 없다.
    """
    # 서식의 이름 칸은 첫 칸이 아닐 수 있다('예비군대대 | 복학원 | 결재') — 칸을 차례로 본다.
    # '복학원'·'신청서'처럼 세 글자 서식 이름은 문서 종류로 끝날 때만 받는다.
    for cell in (_clean_cell(x) for x in _CELL.split(ln)):
        if not cell or not _kind_like(cell):
            continue
        hangul = len(re.findall(r"[가-힣]", cell))
        if hangul >= 5 or (hangul >= 3 and cell.endswith(("서", "표", "원"))):
            return cell
    return None


# 이름 앞의 첨부 표시 — '붙임1.', '[붙임2]', '(서식2-1)', '별첨' 은 문서가 실린 자리이지 이름이 아니다
_ATTACH = re.compile(
    r"^\s*(?:[\[(【]\s*)?(?:붙임|별첨|첨부|별지|서식|양식)\s*(?:제\s*)?[\d\-]*\s*호?\s*(?:[\])】]\s*)?[.\-:]?\s*")
# 이름에 남으면 안 되는 것 — 기호·이모지(★☆※·그림 문자), 파서의 역슬래시 이스케이프, 서로게이트
_SYMBOL = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D★☆※◆◇■□●○▶▷◀◁]")
_MEANINGLESS = re.compile(r"www\.|https?:|[A-Za-z0-9]{16,}")


def tidy_name(name: str) -> str:
    """이름을 정돈한다 — 첨부 표시·기호·이스케이프를 떼고 공백을 고른다. 파일 확장자는 건드리지 않는다."""
    if not name:
        return ""
    stem, ext = (name, "")
    m = re.search(r"\.(pdf|hwpx?|docx?|xlsx?|pptx?|png|jpe?g|txt|md)$", name, re.IGNORECASE)
    if m:
        stem, ext = name[: m.start()], name[m.start():]
    out = stem
    if out.count("+") >= 2 and " " not in out:          # URL 에서 온 이름의 구분자
        out = out.replace("+", " ")
    out = re.sub(r"\\([~*_#\[\]()])", r"\1", out)           # '\~' → '~'
    out = _MD.sub("", out)                                   # '## 제목'·'**굵게**' 의 표시
    out = _SYMBOL.sub(" ", out)
    prev = None
    while prev != out:                                   # '[붙임2] 붙임 …' 처럼 겹친 표시
        prev, out = out, _ATTACH.sub("", out, count=1)
    out = re.sub(r"\(\s+", "(", out); out = re.sub(r"\s+\)", ")", out)   # '( 홈페이지 , 모바일앱 )'
    out = re.sub(r"\s+([,，])\s*", r"\1 ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ·-_,.")
    return (out + ext) if out else name


_PLACEHOLDER = "제목 없음"


def meaningless_filename(filename: str) -> bool:
    """'www.ync.ac.kr__UPLOAD…' 처럼 사람이 붙인 이름이 아닌 것 — 한글이 없고 URL·해시 흔적이 있거나,
    반입이 제목을 못 찾아 붙인 자리 표시('제목 없음 · 접수번호')다. 자리 표시는 다시 읽을 때마다 후보가 된다
    (2026-09-21 실측: 전체 판독 뒤 제목이 본문 첫 줄에 있는데도 자리 표시가 한글이라 그대로 남았다)."""
    stem = Path(filename or "").stem
    if stem.startswith(_PLACEHOLDER):
        return True
    return not re.search(r"[가-힣]", stem) and bool(_MEANINGLESS.search(stem))


# 기관 이름만 있는 줄은 문서의 발신처(레터헤드)다 — '한국장학재단', '통 영 시 장'
_ORG_ONLY = re.compile(
    r"^[가-힣A-Za-z·]{2,20}(?:재단|대학교|대학|공단|공사|협회|센터|위원회|연구원|교육청|[시군구]청|[시군구]장|본부|부|처|청)$")


def _title_like(ln: str) -> bool:
    if _LOOSE_SKIP.match(ln) or _DOC_NUMBER.match(ln) or _DATE_LINE.search(ln):
        return False
    if _ORG_ONLY.match(ln.replace(" ", "")):
        return False
    if not (2 <= len(ln) <= 60) or len(re.findall(r"[가-힣]", ln)) < 2:
        return False
    if len(ln.split()) > 9:                        # 표 머리줄처럼 낱말이 줄줄이 이어진 줄
        return False
    return not re.search(r"(?:다|요|함|음)\s*[.。]?$", ln)   # 문장은 제목이 아니다


_MD = re.compile(r"^\s*#{1,6}\s*|\*\*|__|`")


def _plain(ln: str) -> str:
    """비전 판독 결과는 마크다운이다('## 제목', '**굵게**') — 표시는 이름이 아니다."""
    return re.sub(r"\s{2,}", " ", _MD.sub("", ln)).strip()


def _unspace(ln: str) -> str:
    """'입 찰 공 고' 처럼 글자마다 띄운 제목을 붙인다(모든 토큰이 한 글자일 때만)."""
    toks = ln.split()
    if len(toks) >= 3 and all(len(t) == 1 for t in toks):
        return "".join(toks)
    return ln


def loose_title(text: str) -> str | None:
    """첫머리에서 제목처럼 보이는 첫 줄 — 한글 4자 이상, 60자 이하, 문장·날짜·번호·표 머리줄이 아닌 것.

    첫 줄이 짧고(12자 미만) 다음 줄도 제목처럼 보이면 두 줄로 나뉜 제목으로 보고 잇는다
    ('2025학년도 입학자' + '연계교육과정 편성표').
    """
    lines = [_plain(ln) for ln in (text or "").replace("\r", "\n").split("\n") if _plain(ln)]
    head = lines[:_LOOSE_LINES]
    for i, ln in enumerate(head):
        ln = _unspace(ln)
        if _CELL.search(ln):                 # 표 줄 — 문서 이름 칸이 있으면 그것만 쓴다
            cell = _cell_title(ln)
            if cell:
                return cell
            continue
        hangul = len(re.findall(r"[가-힣]", ln))
        # '이력서'·'신청서'처럼 세 글자 서식 이름은 문서 종류로 끝날 때만 받는다
        if not _title_like(ln) or hangul < 3 or (hangul < 4 and not ln.endswith(("서", "표", "원"))):
            continue
        if len(ln) < 12 and i + 1 < len(head) and not _CELL.search(head[i + 1]):
            nxt = _unspace(head[i + 1])
            # '대상: 재학생'처럼 항목:값 줄은 제목의 뒷줄이 아니다
            if _title_like(nxt) and len(ln) + len(nxt) <= 60 and not re.search(r"[:：]", nxt):
                return f"{ln} {nxt}"
        return ln
    return None


def find_title(text: str) -> tuple[str | None, str | None]:
    """본문 머리에서 (이름, 날짜)를 찾는다. 없으면 각각 None."""
    lines = [_plain(ln) for ln in (text or "").replace("\r", "\n").split("\n") if _plain(ln)]
    head = lines[:_HEAD_LINES]
    title = None
    for ln in head:
        if _NOT_TITLE.match(ln):
            continue
        m = _TITLE_LINE.match(ln)
        if m and re.search(r"[가-힣]{2}", m.group(1)):
            title = re.sub(r"\s{2,}", " ", m.group(1)).strip()
            break
    date = None
    for ln in head:
        m = _DATE_LINE.search(ln)
        if m:
            date = re.sub(r"\s+", " ", m.group(1)).strip()
            break
    return title, date


@lru_cache(maxsize=512)
def _pdf_head(path: str, _mtime: float) -> str:
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(path)
        try:
            return pdf[0].get_textpage().get_text_range() if len(pdf) else ""
        finally:
            pdf.close()
    except Exception:
        return ""


def head_text(stored_path: str | None, fallback: str = "") -> str:
    """첫 쪽 글자층을 우선한다 — 구조 추출은 머리말 줄을 떨어뜨리는 일이 있다."""
    p = Path(stored_path or "")
    if p.suffix.lower() == ".pdf" and p.exists():
        got = _pdf_head(str(p), p.stat().st_mtime)
        if got.strip():
            return got
    return fallback or ""


def document_title(doc: dict) -> tuple[str | None, str | None]:
    """문서 한 건의 (이름, 날짜). 저장된 값이 있으면 그것을 쓴다."""
    import json

    try:
        ident = json.loads(doc.get("identity") or "{}") if isinstance(
            doc.get("identity"), str) else (doc.get("identity") or {})
    except ValueError:
        ident = {}
    if ident.get("title"):
        return ident["title"], ident.get("date")
    return find_title(head_text(doc.get("stored_path"), (doc.get("masked_text") or "")[:3000]))


def _squash(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text)


def title_beats_filename(title: str, filename: str) -> bool:
    """찾은 이름을 파일 이름 대신 쓸지.

    파일 이름에 한글이 없으면('law03.pdf') 무조건 쓴다. 파일 이름이 이미 뜻을 가지면,
    찾은 이름이 그 낱말을 대부분 담을 때만 쓴다 — 제목이 두 줄로 나뉘어 뒷줄만 잡힌
    경우('공동 운영ㆍ관리 매뉴얼')에 멀쩡한 파일 이름을 잘린 이름으로 바꾸지 않기 위해서다.
    """
    stem = Path(filename or "").stem
    if not re.search(r"[가-힣]", stem) or stem.startswith(_PLACEHOLDER):
        return True
    words = [w for w in re.split(r"[^가-힣A-Za-z0-9]+", stem) if len(w) >= 2 and re.search(r"[가-힣]", w)]
    if not words:
        return True
    flat = _squash(title)
    return sum(w in flat for w in words) / len(words) >= 0.75


def resolved_title(doc: dict) -> tuple[str | None, str | None]:
    """파일 이름 대신 쓸 (이름, 날짜). 쓸 만하지 않으면 (None, None)."""
    title, date = document_title(doc)
    filename = doc.get("filename") or ""
    if not title and meaningless_filename(filename):
        # 'www.ync.ac.kr__UPLOAD_PDF_…' 처럼 뜻 없는 이름 — 공지·안내문의 첫 제목 줄이라도 쓴다
        title = loose_title(head_text(doc.get("stored_path"), (doc.get("masked_text") or "")[:3000]))
    if not title or not title_beats_filename(title, filename):
        return None, None
    return tidy_name(title) or title, date


def display_name(doc: dict) -> str:
    """화면에 보일 이름 — '이름 · 날짜', 못 찾으면 파일 이름."""
    title, date = resolved_title(doc)
    if not title:
        return tidy_name(doc.get("filename") or "")
    return f"{title} · {date}" if date else title
