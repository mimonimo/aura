"""문서 이름을 정하는 한 곳 — 반입·DGX 들이기·이름 다시 매기기가 모두 이 규칙을 쓴다.

왜. 올라온 파일 이름에는 사람이 붙인 군더더기가 섞인다 — 앞의 번호('05_', '3.4.2.', '[4-4]', 'p)', '발표3.'),
뒤의 판·날짜·상태('_ver.1', '_1118', '(190424)', '- 복사본', '(최종)', '(○○○ 수정)'), 제출자 이름, 밑줄·확장자.
본문 첫머리에는 대개 진짜 제목이 있지만, 연락처 줄·날짜·쪽 머리글·작성방법 상자도 첫머리에 온다.

규칙은 모두 일반 형식이다. 특정 문서 이름·사람 이름·사업 이름 목록을 두지 않는다.
  1) 본문 제목(표지·첫 제목 줄)이 검사를 통과하면 그것.
  2) 아니면 군더더기를 걷어 낸 파일 이름.
  3) 둘 다 빈약하면 이미 있는 정보로 「<서류 갈래> · <사업> <연도>」.
  4) 그것도 없으면 걷어 낸 파일 이름(빈약해도).
올라온 파일 이름은 identity.original_filename 에 남는다 — 원본 보관·내려받기는 그 이름과 실제 파일을 쓴다.
"""
from __future__ import annotations

import re
from functools import lru_cache

from zzaimy.app.doc_title import (_CELL, _KIND_TAIL, _ORG_ONLY, _cell_title, _plain, _title_like, _unspace,
                                  find_title, meaningless_filename, tidy_name)

# 사람이 손으로 고친 이름 — 다시 매기기(scripts/124)가 건드리지 않는다
USER_SOURCE = "user"

_EXT = re.compile(r"(?:\.(?:hwpx?|pdf|docx?|xlsx?|pptx?|jpe?g|png|tiff?|bmp|webp|txt|md|zip|csv))+$", re.I)

# ── 1. 파일 이름의 앞 군더더기 ─────────────────────────────────────────────
# 뒤에 제목이 이어질 때만 뗀다(떼고 나서 남는 것이 없으면 그대로 둔다).
_LEAD = [
    # '[4-4]', '(3)', '1-30)', '【2】' — 괄호로 닫힌 번호
    re.compile(r"^\s*[\[(【]?\s*\d{1,3}(?:\s*[-–.]\s*\d{1,3})*\s*[\])】]\s*"),
    # '3.4.2.', '1.', '12.5.' — 개요 번호(성분마다 1~2자리라 '2025.10.08' 같은 날짜는 아니다)
    re.compile(r"^\s*\d{1,2}(?:\.\d{1,2})*\.(?!\d)\s*"),
    # '241106_', '20240315-' — 앞에 붙은 날짜 부호(6·8자리). 네 자리(연도)는 아니다
    re.compile(r"^\s*\d{6}(?:\d{2})?\s*[_\-]\s*"),
    # '(공고문) …', '[보도자료 03-10(금)] …' — 앞에 붙은 꼬리표 괄호. 안에 한글 두 글자 이상('(주)'·'(안)' 은 이름의 일부다)
    re.compile(r"^\s*(?:\[[^\[\]]*[가-힣][^\[\]]*[가-힣][^\[\]]*\]|\((?=[^()]*[가-힣][^()]*[가-힣])[^()]{2,20}\))\s*"),
    # '05_', '4_', '07-' — 번호 + 밑줄·붙임표. 네 자리(연도)는 아니다
    re.compile(r"^\s*\d{1,3}\s*[_]\s*|^\s*\d{1,3}\s*-\s+"),
    # '2번 ', '3번_' — 순번 낱말
    re.compile(r"^\s*\d{1,3}\s*번\s*[._)\-]?\s+"),
    # 'p)', 'a.', '가.', '나)' — 목록 기호. 라틴 한 글자는 ')' 일 때만('S.W.' 는 이름이다)
    re.compile(r"^\s*(?:[A-Za-z]\)|[가-하][.)])\s*"),
    # '발표3.', '자료 2_', '안건1)' — 짧은 낱말 + 번호 + 구분자. 'LINC3.0' 처럼 번호 뒤에 숫자가 이어지면 아니다
    re.compile(r"^\s*[가-힣]{1,4}\s*\d{1,3}(?:-\d{1,2})?\s*[.)_\]:]\s*(?!\d)"),
    # '최종!', '[최종]', '(수정)_', '최종_' — 앞에 붙은 상태 표시. 맨 띄어쓰기만으로는 떼지 않는다('최종 결과보고서')
    re.compile(r"^\s*[\[(]\s*(?:최종|수정|확정|완료|초안|가안|제출용?|검토|원본|사본)본?\s*[\])]\s*[_\-]?\s*"
               r"|^\s*(?:최종|수정|확정|완료|초안|가안|제출용?|원본|사본)본?\s*[!_]+\s*"),
]

# ── 2. 파일 이름의 뒤 군더더기 ─────────────────────────────────────────────
_YEARS = r"(?:19|20)\d{2}(?:\s*[-~–]\s*(?:19|20)?\d{2,4})?"
_STATUS_ANY = r"(?:최종|최최종|수정|확정|완료|제출|검토|결재|초안|가안|원본|사본|복사본|OK|ok|Ok|final|Final|FINAL|fin)"
_TAIL = [
    # '- 복사본', ' - 사본 (2)', '_copy'
    re.compile(r"[\s_\-]*(?:복사본|사본|copy|Copy)(?:\s*\(\d{1,2}\))?\s*$"),
    # 윈도 중복 표시 ' (1)' — 앞에 띄어쓰기가 있을 때만
    re.compile(r"\s+\(\d\)\s*$"),
    # '_ver.1', ' v2', '_rev3', 'V1.2'
    re.compile(r"(?:^|[\s_\-(])(?:ver|v|rev|r)\.?\s?\d{1,4}(?:\.\d{1,4})*\.?\s*\)?\s*$", re.I),
    # 날짜·판 번호 꼬리 '_1118', '_0123', '(190424)', '_2022.1123', ' 20251216' — 떼는 쪽은 숫자 덩어리가
    # 연도('_2025')나 연도 범위('_2025-2027')가 아닐 때만(연도는 이름의 정보다)
    re.compile(r"[\s_\-]+\(?(?!" + _YEARS + r"\)?\s*$)\d{4}(?:[.\-]?\d{2,6})*\)?\s*$"),
    re.compile(r"\((?!" + _YEARS + r"\))\d{4,8}\)\s*$"),
    # '(최종)', '_최종', '_OK', '(이○○ 수정)', '(검토완료)', '_수정본', ' - 제출본'
    re.compile(r"\s*[(\[]\s*(?:[가-힣]{2,4}\s*)?" + _STATUS_ANY + r"(?:본|용|완료)?\s*[)\]]\s*$"),
    re.compile(r"[_\-]+\s*" + _STATUS_ANY + r"(?:본|용)?\s*$"),
    re.compile(r"\s+-\s*" + _STATUS_ANY + r"(?:본|용)?\s*$"),
    # 맨 띄어쓰기 뒤에는 뜻이 갈리지 않는 표시만('적합성 검토' 의 '검토' 는 이름이다)
    re.compile(r"\s+(?:최종본?|최최종|수정본|제출본|제출용|OK|ok|final|Final|FINAL)\s*$"),
    # 끝에 남은 느낌표·물결
    re.compile(r"\s*[!~]+\s*$"),
]

# ── 3. 사람 이름(개인정보) ─────────────────────────────────────────────────
# 직위·호칭 — 이름 앞뒤에 붙으면 '사람 표시'라는 강한 단서다. 낱말 목록이 아니라 직책 꼴(…장·…관·…원)로 본다
_ROLE = r"(?:[가-힣]{0,5}(?:장|교수|관|담당자?|위원)|교수님|선생님|님|박사|조교|연구원|주무관)"
_EDIT = r"(?:수정|작성|검토|정리|편집|제출|발표)"
# 이름 같지 않은 끝 글자 — 서류 갈래 꼬리와 개인정보 탐지기의 '이름 아닌 꼬리'
_NOT_NAME_END = tuple(_KIND_TAIL) + ("금", "비", "급", "료", "회", "과", "실", "팀", "관", "소", "청", "화", "식", "업", "사")


@lru_cache(maxsize=1)
def _name_shape() -> re.Pattern:
    """개인정보 탐지기(ingest/pii)의 성명 모양 검사 — 흔한 성씨로 시작하고 서식 낱말이 아닌 2~4글자."""
    from zzaimy.ingest.pii import _NAME_GUARD

    return re.compile(_NAME_GUARD + r"[가-힣]{2,4}")


def _looks_like_name(tok: str, strong: bool, text: str | None) -> bool:
    """사람 이름인지 — 탐지기의 모양 검사를 통과해야 하고, 증거가 하나 더 있어야 한다.

    strong: 직위·호칭·'수정' 같은 사람 표시가 붙어 있다.
    그렇지 않으면 본문이 증거다 — 파일 이름의 그 낱말이 마스킹된 본문 어디에도 없어야 한다(문서의 낱말이라면
    본문에 나온다. 사람 이름은 탐지기가 가렸거나 애초에 본문에 없다). 본문이 없으면 사람 표시가 있을 때만 뗀다.
    """
    if not _name_shape().fullmatch(tok):
        return False
    if tok.endswith(_NOT_NAME_END):
        return False
    if strong:
        return True
    if len(tok) != 3:
        return False
    body = re.sub(r"\s+", "", text or "")
    return len(body) >= 200 and tok not in body


def drop_person(stem: str, text: str | None = None) -> str:
    """파일 이름 앞뒤의 사람 이름(과 붙은 직위·'수정' 표시)을 뗀다. 탐지기가 이름으로 보지 않으면 그대로 둔다."""
    s = stem
    for _ in range(3):
        before = s
        # '(김○○교수)', '(이○○ 수정)', '(홍○○)'
        m = re.search(r"\s*[(\[]\s*([가-힣]{2,4})\s*(" + _ROLE + r"|" + _EDIT + r")?\s*[)\]]\s*$", s)
        if m and _looks_like_name(m.group(1), bool(m.group(2)), text):
            s = s[: m.start()]
            continue
        # '… 사업단장 남○○', '…_김○○', '… 김○○ 교수'
        m = re.search(r"(?:^|[\s_\-])(?:(" + _ROLE + r")\s*)?([가-힣]{3})(?:\s*(" + _ROLE + r"))?\s*$", s)
        if m and _looks_like_name(m.group(2), bool(m.group(1) or m.group(3)), text):
            s = s[: m.start()]
            continue
        # '60안○○', '[4-4]유○○' 처럼 번호에 바로 붙은 이름만 남은 이름 — 학번·순번 + 이름
        m = re.fullmatch(r"\s*\d{1,10}\s*([가-힣]{2,4})\s*", s)
        if m and _looks_like_name(m.group(1), False, text):
            s = ""
            break
        # '김○○_과제계획서' — 앞에 붙은 이름
        m = re.match(r"^\s*([가-힣]{2,4})\s*[_\-]\s*", s)
        if m and _looks_like_name(m.group(1), False, text):
            s = s[m.end():]
            continue
        if s == before:
            break
    return s.strip(" _-·,.")


def _meaningful(s: str) -> bool:
    return len(re.findall(r"[가-힣]", s)) >= 2 or len(re.findall(r"[A-Za-z]", s)) >= 4


def clean_filename(name: str, text: str | None = None) -> str:
    """파일 이름에서 군더더기를 걷어 사람이 읽을 이름으로. 확장자·밑줄도 뗀다.

    text(마스킹된 본문)를 주면 사람 이름 판단에 증거로 쓴다. 아무것도 남지 않으면 빈 문자열.
    """
    s = _EXT.sub("", (name or "").strip())
    s = tidy_name(s)
    s = _EXT.sub("", s)
    for _ in range(6):                                    # 앞 표시가 겹친 것('05_1. …', '[붙임] 3.4.2. …')
        prev = s
        for rx in _LEAD:
            m = rx.match(s)
            if m and m.end() < len(s) and _meaningful(s[m.end():]):
                s = s[m.end():]
        if s == prev:
            break
    # 밑줄 뒤 한두 자리 숫자·라틴 한 글자는 사본·판 표시다('양식_1', '추진계획_F') — 밑줄이 공백으로 바뀌기 전에 본다
    m = re.search(r"_(?:\d{1,2}|[A-Za-z])\s*$", s)
    if m and _meaningful(s[: m.start()]):
        s = s[: m.start()]
    if " " not in s and "+" in s:                        # 누리집 내려받기가 띄어쓰기를 '+' 로 바꾼 이름
        s = s.replace("+", " ")
    s = s.replace("_", " ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    for _ in range(8):                                    # 뒤 표시가 겹친 것('_0123_OK', '- 제출본(190424)')
        prev = s
        for rx in _TAIL:
            m = rx.search(s)
            if m and m.start() > 0 and _meaningful(s[: m.start()]):
                s = s[: m.start()].rstrip(" _-·,.")
        s = drop_person(s, text)
        if s == prev:
            break
    # 다시 한 번 앞 표시 — 뒤를 떼고 나서 드러나는 것('최종! … - 제출본')
    for rx in _LEAD:
        m = rx.match(s)
        if m and m.end() < len(s) and _meaningful(s[m.end():]):
            s = s[m.end():]
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" _-·,.")
    return s


def poor_name(name: str) -> bool:
    """이름 구실을 못 하는 이름 — 비었거나, 뜻 있는 글자가 거의 없거나, 숫자·날짜뿐이거나, URL·자리 표시."""
    s = (name or "").strip()
    if not s or meaningless_filename(s):
        return True
    if _DATE_ONLY.fullmatch(s):
        return True
    hangul = len(re.findall(r"[가-힣]", re.sub(r"\d+\s*(?:번|차|호|쪽|년|월|일)", "", s)))
    latin_words = [w for w in re.findall(r"[A-Za-z]{3,}", s)]
    return hangul < 3 and not latin_words


# ── 4. 본문 제목 후보 검사 ────────────────────────────────────────────────
_CONTACT = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"         # 이메일
    r"|0\d{1,2}[-. )]\d{3,4}[-. ]\d{4}"                        # 전화
    r"|https?://|www\."
    r"|\[(?:KR_[A-Z_]+|EMAIL)\]"                               # 마스킹 자리('[KR_PHONE]')
    # 연락처 항목 — 칸 구분('/', '|') 뒤에 오거나 콜론이 붙은 것만('전화 응대 매뉴얼' 은 제목이다)
    r"|[/|]\s*(?:담당자?|전화|연락처|이메일|E-?mail|FAX|팩스|Tel|문의처?)"
    r"|(?:담당자?|전화|연락처|이메일|E-?mail|FAX|팩스|Tel|문의처?)\s*[:：]",
    re.I)
# 날짜·시각만 있는 줄 — '20251216110841', '2017. 8. 10.', '2025년 10월 8일 (수)'
_DATE_ONLY = re.compile(
    r"\s*(?:\d{8,14}|(?:(?:19|20)?\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]?\s*(?:\d{1,2}\s*[.일]?)?)"
    r"(?:\s*[(（][월화수목금토일][)）])?(?:\s*\d{1,2}\s*[:시]\s*\d{0,2}분?)?)\s*\.?\s*")
# 쪽 머리글·바닥글 — '- 3 -', '3 / 12', 'Page 2', '- i -'
_PAGE_MARK = re.compile(r"^\s*(?:-\s*[\divxIVX]+\s*-|\d+\s*/\s*\d+|page\s*\d+|\d+\s*쪽)\s*$", re.I)
# 작성 안내 상자 — 서식의 '작성방법'·'작성요령'·'※ …' 줄
_GUIDE_BOX = re.compile(r"작성\s*방법|작성\s*요령|기재\s*요령|작성\s*시\s*유의|작성\s*예시|기재\s*방법|작성\s*안내|제출\s*요령"
                        r"|^\s*[※<〈]")
# 구조 이름만 있는 줄 — 목차·순서는 제목이 아니다
_STRUCTURE = re.compile(r"^\s*(?:목\s*차|차\s*례|순\s*서|발표\s*순서|contents?|index|agenda|table of contents)\s*$", re.I)


def title_problem(line: str, text: str = "") -> str | None:
    """제목 후보가 제목이 아닌 까닭. 괜찮으면 None."""
    ln = (line or "").strip()
    if not ln:
        return "빈 줄"
    if _CONTACT.search(ln):
        return "연락처 줄"
    if _DATE_ONLY.fullmatch(ln):
        return "날짜·시각뿐"
    if _PAGE_MARK.match(ln):
        return "쪽 표시"
    if _GUIDE_BOX.search(ln):
        return "작성 안내"
    if _STRUCTURE.match(ln):
        return "목차·순서"
    if _ORG_ONLY.match(ln.replace(" ", "")):
        return "기관 이름뿐"
    if re.fullmatch(r"[가-힣\s]{0,12}표\s*지", ln):
        return "표지 표시"
    flat0 = _squash(ln)
    if re.search(r"(.{8,})\1|(.{8,}).*\2", flat0):
        return "글자층 겹침"                            # PDF 글자층이 같은 글을 두 번 담은 줄('…협의회2024 …협의회')
    if text:
        # 쪽마다 되풀이되는 줄은 머리글이다 — 표지 제목은 한두 번 나온다
        flat = re.sub(r"\s+", " ", ln)
        if flat and len(flat) >= 4 and re.sub(r"\s+", " ", text).count(flat) >= 4:
            return "쪽 머리글(되풀이)"
    return None


# 서류 갈래로 끝나는 제목은 완결된 제목이다 — 갈래 단서는 반입 분류(doc_routing)와 같은 일반 낱말
@lru_cache(maxsize=1)
def _complete_rx() -> re.Pattern:
    from zzaimy.app.doc_routing import _KIND_CUES

    cues = "|".join(f"(?:{v.replace('$', '')})" for v in _KIND_CUES.values())
    # 한 글자 꼬리는 서류 이름에 흔한 것만('…서'·'…표'·'…록'·'…집'·'…증') — '학원'·'방안' 같은 낱말을 완결로 보지 않게
    tails = [t for t in _KIND_TAIL if len(t) >= 2] + ["서", "표", "록", "집", "증"]
    return re.compile(r"(?:" + cues + r"|" + "|".join(map(re.escape, tails)) + r")\s*[)）\]]?\s*$")


def complete_title(s: str) -> bool:
    return bool(_complete_rx().search(s or ""))


_HEAD = 14


def _candidate_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        ln = _plain(raw)
        if ln:
            lines.append(_unspace(ln))
        if len(lines) >= _HEAD:
            break
    return lines


_PRIVATE = re.compile(r"[\uE000-\uF8FF\U000F0000-\U0010FFFF]")       # 한글 문서 글머리 기호의 사용자 영역 글자
_QUOTE = re.compile(r"[「」『』｢｣《》〈〉]")
# 공고·고시 번호 머리 — '교육부 공고 제2026–126호 …'(붙임표는 '-'·'–' 모두)
_DOC_NUMBER_HEAD = re.compile(r"^[가-힣\s]*(?:공고|고시|공지|훈령)\s*(?:제\s*)?\d{2,4}\s*[-–—]\s*\d+\s*호\s*")
_COVER_LABEL = re.compile(r"^[가-힣\s]{0,12}표\s*지\s+")             # '사업계획서 표지 …'


def polish_title(t: str) -> str:
    """본문에서 찾은 제목을 다듬는다 — 글머리 기호·문서 번호 머리·낫표·표지 표시·상태 꼬리."""
    s = _PRIVATE.sub(" ", t or "")
    m = _DOC_NUMBER_HEAD.match(s)
    if m and _meaningful(s[m.end():]):
        s = s[m.end():]
    m = _COVER_LABEL.match(s)
    if m and _meaningful(s[m.end():]):
        s = s[m.end():]
    s = _QUOTE.sub(" ", s)
    s = re.sub(r"\s+([)\]])", r"\1", re.sub(r"([(\[])\s+", r"\1", s))
    s = re.sub(r"\s{2,}", " ", s).strip(" ·-_,.")
    for _ in range(3):
        prev = s
        for rx in _TAIL:
            m = rx.search(s)
            if m and m.start() > 0 and _meaningful(s[: m.start()]):
                s = s[: m.start()].rstrip(" _-·,.")
        if s == prev:
            break
    return tidy_name(s)


def body_title(text: str) -> tuple[str | None, str | None]:
    """본문 첫머리의 제목과 기준 날짜.

    규정식 제목(…규정·지침, 윗줄 잇기 포함)을 먼저 보고, 없으면 첫 제목다운 줄을 본다. 제목다운 줄이 서류 갈래로
    끝나지 않으면 다음 줄(최대 두 줄)과 이어 완결된 제목이 되는지 본다 — 표지 제목은 두세 줄로 갈라진다.
    """
    reg, date = find_title(text)
    if reg and not title_problem(reg, text):
        return polish_title(reg), date
    lines = _candidate_lines(text)
    for i, ln in enumerate(lines):
        if _CELL.search(ln):
            cell = _cell_title(ln)
            if cell and not title_problem(cell, text):
                return polish_title(cell), date
            continue
        if title_problem(ln, text) or not _title_like(ln):
            continue
        hangul = len(re.findall(r"[가-힣]", ln))
        if hangul < 3 or (hangul < 4 and not ln.endswith(("서", "표", "원"))):
            continue
        title = ln
        if not complete_title(ln):
            joined, used = ln, 0
            for nxt in lines[i + 1: i + 3]:
                if _CELL.search(nxt) or title_problem(nxt, text) or not _title_like(nxt) or re.search(r"[:：]", nxt):
                    break
                # 괄호로 감싼 줄은 부제다(같은 제목의 문서를 가르는 말 — db._unique_name 이 쓴다).
                # 판독 결과는 제목을 두 번 적기도 한다 — 같은 제목으로 시작하는 줄은 뒷줄이 아니다
                if re.match(r"^\s*[(（\[]", nxt) or _squash(nxt).startswith(_squash(ln)):
                    break
                if len(joined) + 1 + len(nxt) > 90:
                    break
                joined, used = f"{joined} {nxt}", used + 1
                if complete_title(joined):
                    title = joined
                    break
            else:
                # 갈래 낱말로 끝나지 않아도 첫 줄이 짧으면(12자 미만) 다음 줄과 잇는다 — 예전 규칙
                if used and len(ln) < 12:
                    title = f"{ln} {lines[i + 1]}"
        return polish_title(title), date
    return None, date


def _words(s: str) -> set[str]:
    return {w for w in re.split(r"[^가-힣A-Za-z0-9]+", s or "") if len(w) >= 2 and re.search(r"[가-힣A-Za-z]", w)}


def _squash(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")


def body_beats_file(title: str, fname: str) -> bool:
    """본문 제목을 파일 이름 대신 쓸지.

    파일 이름이 빈약하면 쓴다. 파일 이름이 뜻을 가지면 본문 제목이 완결된 서류 이름(서류 갈래 낱말로 끝남)이고
    파일 이름과 같은 문서임을 보일 때만 쓴다 — 세 글자 이상 낱말 하나를 함께 갖거나, 파일 이름 낱말의 삼분의 일 이상을 담을 때.
    본문 제목이 파일 이름의 일부만 잡은 것(제목 뒷줄만 잡힘)이면 더 온전한 파일 이름을 둔다.
    """
    if poor_name(fname):
        return True
    if not complete_title(title):
        return False
    tq, fq = _squash(title), _squash(fname)
    if tq and tq in fq and len(tq) < len(fq):
        return False                                              # 파일 이름이 본문 제목을 다 담고 더 길다
    fw = [w for w in _words(fname) if re.search(r"[가-힣]", w)]
    if not fw:
        return True                                               # 파일 이름에 한글 낱말이 없다('report_final')
    hit = [w for w in fw if w in tq]
    # 서류 갈래 낱말('확인서'·'계획서')만 겹치는 것은 같은 문서의 증거가 아니다 — 여러 서식이 묶인 파일의 첫 서식 이름일 수 있다
    return any(len(w) >= 3 and not complete_title(w) for w in hit) or len(hit) * 3 >= len(fw)


def meta_name(meta: dict | None) -> str:
    """「<서류 갈래> · <사업> <연도>」 — 이미 있는 정보(갈래·사업 분류·연도)로만 만든다."""
    meta = meta or {}
    kind = (meta.get("kind_label") or "").strip()
    prog = (meta.get("program") or "").strip()
    year = str(meta.get("year") or "").strip()
    if not kind and not prog:
        return ""
    tail = " ".join(x for x in (prog, year) if x)
    return " · ".join(x for x in (kind or "문서", tail) if x)


def choose_name(original: str, text: str = "", meta: dict | None = None,
                ask=None) -> dict:
    """문서 이름을 고른다 — {name, title, date, source}. source: body · model · file · meta.

    ask 를 주면, 본문 규칙이 제목을 못 찾았고 파일 이름도 빈약할 때 모델에게 본문의 제목 줄을 짚게 한다
    (doc_identity.find_title_by_model — 본문에 있는 줄만 받는다).
    """
    fname = clean_filename(original, text or None)
    title, date = body_title(text) if text else (None, None)
    if title and body_beats_file(title, fname):
        return {"name": title, "title": title, "date": date, "source": "body"}
    if not title and ask is not None and poor_name(fname) and text:
        from zzaimy.app.doc_identity import find_title_by_model

        got = find_title_by_model(text, ask)
        got = tidy_name(got) if got else None
        if got and not title_problem(got, text):
            return {"name": got, "title": got, "date": None, "source": "model"}
    if not poor_name(fname):
        return {"name": fname, "title": None, "date": None, "source": "file"}
    alt = meta_name(meta)
    if alt:
        return {"name": alt, "title": None, "date": None, "source": "meta"}
    return {"name": fname, "title": None, "date": None, "source": "file"}


def naming_meta(db, doc: dict) -> dict:
    """대체 이름에 쓸 정보 — 서류 갈래(documents.kind), 원본 장부의 사업 이름·연도. 없으면 빈 값."""
    from zzaimy.app.doc_routing import KINDS

    out = {"kind_label": KINDS.get(doc.get("kind") or "", "")}
    sp = str(doc.get("stored_path") or "")
    try:
        with db._conn() as conn:
            row = None
            if sp.startswith("dgx://"):
                row = conn.execute("SELECT program_name, year FROM archive_files WHERE rel = ?", (sp[6:],)).fetchone()
            if row is None:
                row = conn.execute("SELECT program_name, year FROM archive_files WHERE doc_id = ?",
                                   (doc.get("id"),)).fetchone()
    except Exception:                                    # 원본 장부가 없는 DB(시험·DGX 임시) — 갈래만
        row = None
    if row is not None:
        prog = str(row[0] or "")
        out["program"] = "" if prog in ("", "사업 미분류", "미분류") else prog
        y = str(row[1] or "")
        out["year"] = y if re.fullmatch(r"(?:19|20)\d{2}", y) else ""
    return out
