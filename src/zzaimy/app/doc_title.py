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
_LOOSE_SKIP = re.compile(r"^(?:[\d\-–.()\s/]+$|제\s*\d+\s*[장조절]|\d+[.)]|[□○◦▪■●※▶-]|페이지|page)",
                         re.IGNORECASE)


def _title_like(ln: str) -> bool:
    if _LOOSE_SKIP.match(ln) or _DATE_LINE.search(ln):
        return False
    if not (2 <= len(ln) <= 60) or len(re.findall(r"[가-힣]", ln)) < 2:
        return False
    if len(ln.split()) > 9:                        # 표 머리줄처럼 낱말이 줄줄이 이어진 줄
        return False
    return not re.search(r"(?:다|요|함|음)\s*[.。]?$", ln)   # 문장은 제목이 아니다


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
    lines = [re.sub(r"\s{2,}", " ", ln.strip())
             for ln in (text or "").replace("\r", "\n").split("\n") if ln.strip()]
    head = lines[:_HEAD_LINES]
    for i, ln in enumerate(head):
        ln = _unspace(ln)
        if not _title_like(ln) or len(re.findall(r"[가-힣]", ln)) < 4 or len(ln) < 4:
            continue
        if len(ln) < 12 and i + 1 < len(head):
            nxt = _unspace(head[i + 1])
            if _title_like(nxt) and len(ln) + len(nxt) <= 60:
                return f"{ln} {nxt}"
        return ln
    return None


def find_title(text: str) -> tuple[str | None, str | None]:
    """본문 머리에서 (이름, 날짜)를 찾는다. 없으면 각각 None."""
    lines = [ln.strip() for ln in (text or "").replace("\r", "\n").split("\n") if ln.strip()]
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
    if not re.search(r"[가-힣]", stem):
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
    if not title and not re.search(r"[가-힣]", Path(filename).stem):
        # 'www.ync.ac.kr__UPLOAD_PDF_…' 처럼 뜻 없는 이름 — 공지·안내문의 첫 제목 줄이라도 쓴다
        title = loose_title(head_text(doc.get("stored_path"), (doc.get("masked_text") or "")[:3000]))
    if not title or not title_beats_filename(title, filename):
        return None, None
    return title, date


def display_name(doc: dict) -> str:
    """화면에 보일 이름 — '이름 · 날짜', 못 찾으면 파일 이름."""
    title, date = resolved_title(doc)
    if not title:
        return doc.get("filename") or ""
    return f"{title} · {date}" if date else title
