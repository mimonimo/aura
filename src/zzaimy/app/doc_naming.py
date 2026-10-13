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
from zzaimy.doc_formats import ARCHIVE_EXT as _ARC, DOC_EXT as _DOC, IMAGE_EXT as _IMG

# 사람이 손으로 고친 이름 — 다시 매기기(scripts/124)가 건드리지 않는다
USER_SOURCE = "user"

# 확장자 — 처리하는 문서·그림·압축 형식 목록 한 곳(doc_formats)에서. 새 형식을 들이면 이름에서도 떨어진다
_EXT = re.compile(r"(?:\.(?:" + "|".join(sorted({*_DOC, *_IMG, *_ARC, "md", "hwt"}, key=len, reverse=True)) + r"))+$", re.I)

# ── 1. 파일 이름의 앞 군더더기 ─────────────────────────────────────────────
# 뒤에 제목이 이어질 때만 뗀다(떼고 나서 남는 것이 없으면 그대로 둔다).
_LEAD = [
    # '1.1차년도 …', '3. 2차년도 …' — 목록 번호 뒤에 'N차년도' 가 이어지는 것(개요 번호 '1.1' 이 아니다)
    re.compile(r"^\s*\d{1,2}\.\s*(?=\d{1,2}\s*차\s*(?:년도|연도))"),
    # '[4-4]', '(3)', '1-30)', '【2】' — 괄호로 닫힌 번호
    re.compile(r"^\s*[\[(【]?\s*\d{1,3}(?:\s*[-–.]\s*\d{1,3})*\s*[\])】]\s*"),
    # '3.2020 가나사업 …', '11.2025년 …' — 목록 번호 바로 뒤의 연도
    re.compile(r"^\s*\d{1,2}\.\s*(?=(?:19|20)\d{2}(?!\d))"),
    # '3.4.2.', '1.', '12.5.' — 개요 번호(성분마다 1~2자리라 '2025.10.08' 같은 날짜는 아니다)
    re.compile(r"^\s*\d{1,2}(?:\.\d{1,2})*\.(?!\d)\s*"),
    # '241106_', '20240315-' — 앞에 붙은 날짜 부호(6·8자리). 네 자리(연도)는 아니다
    re.compile(r"^\s*\d{6}(?:\d{2})?\s*[_\-]\s*"),
    # '(공고문) …', '[보도자료 03-10(금)] …' — 앞에 붙은 꼬리표 괄호. 안에 한글 두 글자 이상('(주)'·'(안)' 은 이름의 일부다)
    re.compile(r"^\s*(?:\[[^\[\]]*[가-힣][^\[\]]*[가-힣][^\[\]]*\]|\((?=[^()]*[가-힣][^()]*[가-힣])[^()]{2,20}\))\s*"),
    # '117.가나병원', '18-가나과' — 한글 이름에 바로 붙은 순번(점·붙임표). 네 자리(연도)·'3.4.2.' 은 위·아래 규칙이 본다
    re.compile(r"^\s*\d{1,3}[.\-](?=[가-힣])"),
    # '05_', '4_', '07-' — 번호 + 밑줄·붙임표. 네 자리(연도)는 아니다
    re.compile(r"^\s*\d{1,3}\s*[_]\s*|^\s*\d{1,3}\s*-\s+"),
    # '2번 ', '3번_' — 순번 낱말
    re.compile(r"^\s*\d{1,3}\s*번\s*[._)\-]?\s+"),
    # 'p)', 'a.', '가.', '나)' — 목록 기호. 라틴 한 글자는 ')' 일 때만('S.W.' 는 이름이다)
    re.compile(r"^\s*(?:[A-Za-z]\)|[가-하][.)])\s*"),
    # '발표3.', '자료 2_', '안건1)' — 짧은 낱말 + 번호 + 구분자. 'LINC3.0' 처럼 번호 뒤에 숫자가 이어지면 아니다
    re.compile(r"^\s*[가-힣]{1,4}\s*\d{1,3}(?:-\d{1,2})?\s*[.)\]:]\s*(?!\d)"),   # 밑줄은 아니다('가나30_…' 은 이름)
    # '최종!', '[최종]', '(수정)_', '최종_' — 앞에 붙은 상태 표시. 맨 띄어쓰기만으로는 떼지 않는다('최종 결과보고서')
    re.compile(r"^\s*[\[(]\s*(?:최종|수정|확정|완료|초안|가안|제출용?|검토|원본|사본)본?\s*[\])]\s*[_\-]?\s*"
               r"|^\s*(?:최종|수정|확정|완료|초안|가안|제출용?|원본|사본)본?\s*[!_]+\s*"),
]

# ── 2. 파일 이름의 뒤 군더더기 ─────────────────────────────────────────────
_YEARS = r"(?:19|20)\d{2}(?:\s*[-~–]\s*(?:19|20)?\d{2,4})?"
# 행정 문서의 발송·날인 상태 — '직인X', '직인없음', '직인 미포함', '날인본', '발송본'(오타 '직임' 포함)
_SEAL = r"(?:직[인임]\s*(?:X|x|×|없음|미포함|제외|생략|포함|본)?|날인\s*(?:본|없음|미포함)?|발송본?|송부본?)"
_STATUS_ANY = r"(?:최종|최최종|수정|확정|완료|제출|검토|결재|초안|가안|원본|사본|복사본|OK|ok|Ok|final|Final|FINAL|fin)"
_TAIL = [
    # '- 복사본', ' - 사본 (2)', '_copy'
    re.compile(r"[\s_\-]+(?:복사본|사본|copy|Copy)(?:\s*\(\d{1,2}\))?\s*$"),   # 띄어 쓴 것만 — '통장사본' 은 서류 이름
    # 윈도 중복 표시 ' (1)' — 앞에 띄어쓰기가 있을 때만
    re.compile(r"\s+\(\d\)\s*$"),
    # '_ver.1', ' v2', '_rev3', 'V1.2'
    re.compile(r"(?:^|[\s_\-(])(?:ver|v|rev|r)\.?\s?\d{1,4}(?:\.\d{1,4})*\.?\s*\)?\s*$", re.I),
    # 날짜·판 번호 꼬리 '_1118', '_0123', '(190424)', '_2022.1123', ' 20251216' — 떼는 쪽은 숫자 덩어리가
    # 연도('_2025')나 연도 범위('_2025-2027')가 아닐 때만(연도는 이름의 정보다)
    re.compile(r"[\s_\-]+\(?(?!" + _YEARS + r"\)?\s*$)\d{4}(?:[.\-]?\d{2,6})*\)?\s*$"),
    re.compile(r"\((?!" + _YEARS + r"\))\d{4,8}\)\s*$"),
    # '(01.27)', '(3.30.)' — 괄호 속 월.일
    re.compile(r"\s*\(\s*\d{1,2}\.\d{1,2}\.?\s*\)\s*$"),
    # '계획서170922' — 이름에 붙은 YYMMDD(월 01~12, 일 01~31)
    re.compile(r"(?<=[가-힣A-Za-z)])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:\s+\d{1,4})?\s*$"),   # 뒤에 남은 짧은 번호까지
    # '_직인X', ' 직인없음', '(직인 미포함)' — 발송·날인 상태
    re.compile(r"[\s_\-]*[(\[]?\s*" + _SEAL + r"\s*[)\]]?\s*$"),
    # '발송0808', '_최종본03300921' — 상태 낱말에 붙은 날짜
    re.compile(r"[\s_\-]*(?:발송|송부|접수|" + _STATUS_ANY + r")(?:본|용)?\s*\d{4,8}\s*$"),
    # '(최종)', '_최종', '_OK', '(이○○ 수정)', '(검토완료)', '_수정본', ' - 제출본', '최종(직인미포함)'
    re.compile(r"\s*[(\[]\s*(?:[가-힣]{2,4}\s*)?" + _STATUS_ANY + r"(?:본|용|완료)?\s*[)\]]\s*$"),
    re.compile(r"[_\-]+\s*" + _STATUS_ANY + r"(?:본|용)?\s*$"),
    re.compile(r"\s+-\s*" + _STATUS_ANY + r"(?:본|용)?\s*$"),
    # 맨 띄어쓰기 뒤에는 뜻이 갈리지 않는 표시만('적합성 검토' 의 '검토' 는 이름이다)
    re.compile(r"\s+(?:최종본?|최최종|수정본|제출본|제출용|OK|ok|final|Final|FINAL)\s*$"),
    # '최종Ver', '수정 ver2' — 상태 낱말에 붙은 판 표시
    re.compile(r"[\s_\-]+(?:최종|수정|제출)\s*(?:ver|v)\.?\s*\d{0,3}\s*$", re.I),
    # '오전 14시30분', '오후 3시' — 저장한 시각
    re.compile(r"[\s_\-]+(?:오전|오후|AM|PM)?\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?\s*$", re.I),
    # 한글 바로 뒤 붙임표 번호('…박○○-2', '…표-1') — 사본·순번 표시. 숫자 뒤('1-2')는 항목 번호라 두지 않는다
    re.compile(r"(?<=[가-힣])\s*-\s*\d{1,2}\s*$"),
    # '(빨간색)', '(파랑)' — 검토할 때 칠한 색 표시
    re.compile(r"\s*\(\s*(?:빨간|빨강|파란|파랑|노란|노랑|초록|녹색|주황|보라|분홍|회|검정|검은)\s*색?\s*(?:표시)?\s*\)\s*$"),
    # 끝에 남은 느낌표·물결
    re.compile(r"\s*[!~]+\s*$"),
]

# ── 3. 사람 이름(개인정보) ─────────────────────────────────────────────────
# 직위·호칭 — 이름 앞뒤에 붙으면 '사람 표시'라는 강한 단서다. 낱말 목록이 아니라 직책 꼴(…장·…관·…원)로 본다
_ROLE = r"(?:[가-힣]{0,5}(?:장|교수|관|담당자?|위원)|교수님|선생님|님|박사|조교|연구원|주무관)"
_EDIT = r"(?:수정|작성|검토|정리|편집|제출|발표)"
# 이름 같지 않은 끝 글자 — 서류 갈래 꼬리와 개인정보 탐지기의 '이름 아닌 꼬리'
_NOT_NAME_END = tuple(_KIND_TAIL) + ("금", "비", "급", "료", "회", "과", "실", "팀", "관", "소", "청", "화", "업", "사",
                                          # 이름에 드문 끝 글자 — 보통 명사의 꼬리('한국어'·'방법론'·'제작물'·'기술'·'제품')
                                          "어", "론", "법", "술", "품", "물", "류", "권", "률", "율")


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


@lru_cache(maxsize=1)
def _titles() -> str:
    """직위 낱말 — 개인정보 탐지기의 직위 목록(pii._TITLES)에 사업단 직책 몇 개를 더한 것."""
    from zzaimy.ingest.pii import _TITLES

    words = sorted({t.replace(" ", "") for t in _TITLES} | {"단장", "사업단장", "부단장", "위원장", "위원", "교수님", "선생님"},
                   key=len, reverse=True)
    return "|".join(map(re.escape, words))


_UNIT_END = re.compile(r"(?:학과|계열|전공|과|팀|부|처|실|센터|사업단|대학교|대학)$")


def _after_unit(head: str, m) -> bool:
    """'가나학과-김○○' 처럼 소속(학과·계열·팀 …) 바로 뒤에 붙임표·밑줄로 붙은 이름 — 구조 증거."""
    sep = m.group(0)[:1]
    return sep in "_-" and bool(_UNIT_END.search(head.strip()))


def drop_person(stem: str, text: str | None = None, whole_ok: bool = False) -> str:
    """파일 이름 앞뒤의 사람 이름(과 붙은 직위·'수정' 표시)을 뗀다. 탐지기가 이름으로 보지 않으면 그대로 둔다."""
    s = stem
    for _ in range(3):
        before = s
        # 이름과 직위가 함께 든 괄호('(남○○ 단장)', '(교수 김○○)')는 자리와 상관없이 사람 표시다(직위는 탐지기 목록·직책 꼴)
        m = re.search(r"\s*[(\[]\s*(?:(" + _titles() + r")\s+)?([가-힣]{2,4})(?:\s*(" + _titles() + r"))?\s*[)\]]", s)
        if m and (m.group(1) or m.group(3)) and _looks_like_name(m.group(2), True, text):
            s = re.sub(r"\s{2,}", " ", (s[: m.start()] + " " + s[m.end():])).strip()
            continue
        # 닫는 괄호 바로 뒤 끝에 붙은 세 글자 이름('…(주관) 김○○')
        m = re.search(r"(?<=[)\]])\s*([가-힣]{3})\s*$", s)
        if m and _looks_like_name(m.group(1), True, text):
            s = s[: m.start()]
            continue
        # '(김○○교수)', '(이○○ 수정)', '(홍○○)' — 끝의 괄호에 세 글자 이름만 있는 자리는 그 자체가 구조 증거다
        m = re.search(r"\s*[(\[]\s*([가-힣]{2,4})\s*(" + _ROLE + r"|" + _EDIT + r")?\s*[)\]]\s*$", s)
        if m and _looks_like_name(m.group(1), bool(m.group(2)) or len(m.group(1)) == 3, text):
            s = s[: m.start()]
            continue
        # '(가나학과_김○○)' — 끝 괄호 안에서 소속 뒤에 붙은 이름은 이름만 뗀다
        m = re.search(r"[_\s\-]([가-힣]{3})\s*\)\s*$", s)
        if m and "(" in s[: m.start()] and _looks_like_name(m.group(1), True, text):
            s = s[: m.start()] + ")"
            continue
        # '… 사업단장 남○○', '…_김○○', '… 김○○ 교수' — 앞 직위는 띄어 쓴 것만('장한건설' 의 '장' 은 직위가 아니다)
        m = re.search(r"(?:^|[\s_\-])(?:(" + _ROLE + r")\s+)?([가-힣]{3})(?:\s*(" + _ROLE + r"))?\s*$", s)
        # 이름 하나만 남은 것('전단지')은 번호가 붙어 있던 파일 이름('[4-4]유○○')일 때만 사람 이름으로 본다
        if m and (m.start() > 0 or whole_ok) \
                and _looks_like_name(m.group(2), bool(m.group(1) or m.group(3)) or _after_unit(s[: m.start()], m)
                                     or (m.start() == 0 and whole_ok)
                                     or (m.start() > 0 and complete_title(s[: m.start()].strip())), text):
            s = s[: m.start()]
            continue
        # '60안○○', '[4-4]유○○' 처럼 번호에 바로 붙은 이름만 남은 이름 — 학번·순번 + 이름
        m = re.fullmatch(r"\s*\d{1,10}\s*([가-힣]{2,4})\s*", s)
        if m and _looks_like_name(m.group(1), whole_ok and len(m.group(1)) == 3, text):
            s = ""
            break
        # '김○○_과제계획서' — 앞에 붙은 이름. 뒤가 서류 이름(갈래 낱말로 끝남)이고 세 글자면 구조 증거다
        m = re.match(r"^\s*([가-힣]{2,4})\s*[_\-]\s*", s)
        if m and _looks_like_name(m.group(1), len(m.group(1)) == 3 and complete_title(s[m.end():]), text):
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
    # '[4-4]유○○', '60안○○' — 괄호 번호나 숫자에 바로 붙은 세 글자뿐인 이름(순번·학번 + 사람). '00-전단지' 는 아니다
    numbered = bool(re.fullmatch(r"\s*(?:[\[(【]\s*[\d\-]+\s*[\])】]|\d+)\s*[가-힣]{3}\s*", s))
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
    # '김○○_교육과정표' — 밑줄로 붙은 앞 이름은 밑줄이 공백으로 바뀌기 전에 본다(뒤가 서류 이름일 때만 구조 증거)
    m = re.match(r"^\s*([가-힣]{3})\s*_\s*", s)
    if m and _looks_like_name(m.group(1), complete_title(s[m.end():].replace("_", " ")), text):
        s = s[m.end():]
    s = s.replace("_", " ")
    s = re.sub(r"\s*#\s*", " ", s)                      # '2021#가나대#자체진단보고서#…' — 해시 기호를 칸 구분으로 쓴 이름
    s = re.sub(r"\s{2,}", " ", s).strip()
    for _ in range(8):                                    # 뒤 표시가 겹친 것('_0123_OK', '- 제출본(190424)')
        prev = s
        for rx in _TAIL:
            m = rx.search(s)
            if m and m.start() > 0 and _meaningful(s[: m.start()]):
                s = s[: m.start()].rstrip(" _-·,.")
        s = drop_person(s, text, whole_ok=numbered)
        if s == prev:
            break
    # 다시 한 번 앞 표시 — 뒤를 떼고 나서 드러나는 것('최종! … - 제출본')
    for rx in _LEAD:
        m = rx.match(s)
        if m and m.end() < len(s) and _meaningful(s[m.end():]):
            s = s[m.end():]
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    # 꼬리를 떼다 괄호 안이 잘렸으면('… 활동(ver') 닫히지 않은 괄호 조각도 뗀다
    m = re.search(r"\s*\([^()]*$", s)
    if m and _meaningful(s[: m.start()]):
        s = s[: m.start()]
    s = re.sub(r"\s{2,}", " ", s).strip(" _-·,.")
    return s


def poor_name(name: str) -> bool:
    """이름 구실을 못 하는 이름 — 비었거나, 뜻 있는 글자가 거의 없거나, 숫자·날짜뿐이거나, URL·자리 표시."""
    s = (name or "").strip()
    if not s or meaningless_filename(s):
        return True
    if _DATE_ONLY.fullmatch(s):
        return True
    hangul = len(re.findall(r"[가-힣]", re.sub(r"\d+\s*(?:번|차|호|쪽|년|월|일|시|분)|오전|오후", "", s)))   # 날짜·시각 낱말은 뜻이 아니다
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
    # 장식으로 시작하는 줄 — 낱자모('ㅣ')·붙임표·기호. 제목은 글자·숫자·여는 괄호·따옴표로 시작한다
    if not re.match(r"[가-힣A-Za-z0-9(\[「『｢《〈'\"‘“]", ln):
        return "장식 머리"
    # 붙임표로 감싼 구절 '- 지역과 대학의 새로운 길 -' 은 표지의 구호다
    if re.search(r"(?:^|\s)[-–—~]\s*[^-–—~\s][^-–—~]{2,}?\s[-–—~](?:\s|$)", ln):
        return "구호"
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


# ── 본문 제목의 모양 관문 — 파일 이름을 대신할 제목은 명사구 서류 이름이어야 한다 ─────────────
_HONORIFIC = re.compile(r"님(?=[\s)\]]|$)|귀하|귀중")
_APPROVAL_WORDS = re.compile(r"문서\s*번호|(?:기안|결재|전결|협조|수신|발신)(?:\s|$).*(?:팀장|처장|과장|총장|실장|국장|부장|원장)")
_FIELD_LABELS = ("생년월일", "주민등록번호", "성명", "연락처", "휴대전화", "주소", "학번", "여권번호", "이메일", "전화번호")
# 메일·메신저 머리 칸 이름 — 둘 이상 이어지면 머리 칸 줄이다
_MAIL_LABELS = ("보낸사람", "받는사람", "참조", "보낸시간", "읽은시간", "받은시간", "발신자", "수신자", "숨은참조", "제목")
_OPEN, _CLOSE = "([「『｢［", ")]」』｣］"


def shape_problem(t: str) -> str | None:
    """제목 모양이 아닌 까닭 — 호칭·결재란·항목 이름 나열·괄호 조각·구호·서류 낱말 없는 명사 나열·같은 말 되풀이."""
    s = (t or "").strip()
    if not s:
        return "빈 제목"
    if _HONORIFIC.search(s):
        return "호칭·수신 표시"
    if _APPROVAL_WORDS.search(s):
        return "결재란"
    if sum(lab in s.replace(" ", "") for lab in _FIELD_LABELS) >= 2 or re.search(r"[가-힣]{2,}\s*/\s*[A-Za-z가-힣]{2,}.*/", s) \
            or re.search(r"[가-힣]{2,}\s*/\s*[A-Za-z]{2,}", s):
        return "항목 이름 나열"
    if s.count("√") or sum(lab in s.replace(" ", "") for lab in _MAIL_LABELS) >= 2:
        return "메일 머리"
    if any(s.count(o) != s.count(c) for o, c in zip(_OPEN, _CLOSE)) or re.search(r"[\[［(]\s*[\]］)]", s):
        return "괄호 조각"
    if re.match(r"[“”\"‘]|'(?!\d)", s):
        return "따옴표로 여는 구호"
    if not has_kind_word(s):
        return "서류 낱말 없음"
    return None


def repeats(s: str) -> bool:
    """같은 연도(또는 연도 둘)·같은 낱말(세 글자 이상)이 되풀이되는지 — 여러 줄을 이을 때 표 머리·부제가 섞인 표시."""
    years = re.findall(r"(?<![\d~\-])((?:19|20)\d{2})(?![\d~\-])", s or "")
    if len(years) >= 2:
        return True
    flat = re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")
    return bool(re.search(r"([가-힣A-Za-z]{3,}).+\1", flat))


# 서류 갈래로 끝나는 제목은 완결된 제목이다 — 갈래 단서는 반입 분류(doc_routing)와 같은 일반 낱말
@lru_cache(maxsize=1)
def _complete_rx() -> re.Pattern:
    from zzaimy.app.doc_routing import _KIND_CUES

    cues = "|".join(f"(?:{v.replace('$', '')})" for v in _KIND_CUES.values())
    # 한 글자 꼬리는 서류 이름에 흔한 것만('…서'·'…표'·'…록'·'…집'·'…증') — '학원'·'방안' 같은 낱말을 완결로 보지 않게
    tails = [t for t in _KIND_TAIL if len(t) >= 2] + ["서", "표", "록", "집", "증"]
    return re.compile(r"(?:" + cues + r"|" + "|".join(map(re.escape, tails)) + r")\s*[)）\]]?\s*$")


@lru_cache(maxsize=1)
def _kind_anywhere() -> re.Pattern:
    from zzaimy.app.doc_routing import _KIND_CUES

    cues = "|".join(f"(?:{v.replace('$', '')})" for v in _KIND_CUES.values())
    return re.compile(r"(?:" + cues + r"|" + "|".join(map(re.escape, [t for t in _KIND_TAIL if len(t) >= 2])) + r"|[가-힣]{2,}[서표록증])")


# 서류를 가리키는 일반 명사 — 갈래 단서(doc_routing)에 없는 것('절차'·'자료'·'개요' …)
_DOC_NOUNS = ("절차", "자료", "개요", "요약", "소개", "설명", "가이드", "프로그램", "교육과정", "협약", "심의", "평가", "조사", "설문",
              "결과", "보고", "일정", "목록", "명세", "내역", "기록", "공문", "발표", "회의", "현황", "계획", "안내", "지침", "규정")


def has_kind_word(s: str) -> bool:
    """서류를 가리키는 낱말이 어디든 있는지('…교육과정표 … (주간)' 처럼 끝이 아니어도)."""
    return bool(_kind_anywhere().search(s or "")) or any(n in (s or "") for n in _DOC_NOUNS)


def complete_title(s: str) -> bool:
    return bool(_complete_rx().search(s or ""))


_HEAD = 14


@lru_cache(maxsize=1)
def _header_words() -> frozenset:
    """표 머리 칸 낱말 — 개인정보 탐지기의 서식 낱말(pii._FORM_WORDS)에 분류 머리 몇 개를 더한 일반 어휘."""
    from zzaimy.ingest.pii import _FORM_WORDS

    return frozenset(_FORM_WORDS) | {"항목", "분류", "영역", "지표", "구분", "사업명", "과제명", "문서명", "건명"}


def strip_header(ln: str) -> str:
    """'구분 전문대 첨단산업 … 사업계획서' 처럼 표 머리 칸 낱말이 줄 앞에 붙은 것을 뗀다(뒤에 이름이 남을 때만)."""
    # '제 목 …'(칸 이름처럼 띄어 쓴 것)·'제목: …' 만 공문의 제목 칸 이름이다 — '제목 없는 …' 은 이름의 낱말
    ln = re.sub(r"^(?:제\s목\s+|제\s?목\s*[:：]\s*)(?=\S)", "", ln)
    toks = ln.split(None, 1)
    if len(toks) == 2 and re.sub(r"[:：|]$", "", toks[0]) in _header_words() and _meaningful(toks[1]):
        return toks[1].lstrip(":：| ")
    return ln


def _candidate_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        ln = _plain(raw)
        if ln:
            lines.append(strip_header(_unspace(ln)))
        if len(lines) >= _HEAD:
            break
    return lines


_PRIVATE = re.compile(r"[\uE000-\uF8FF\U000F0000-\U0010FFFF]")       # 한글 문서 글머리 기호의 사용자 영역 글자
_QUOTE = re.compile(r"[「」『』｢｣《》〈〉]")
# 공고·고시 번호 머리 — '교육부 공고 제2026–126호 …'(붙임표는 '-'·'–' 모두)
_DOC_NUMBER_HEAD = re.compile(r"^[가-힣\s]*(?:공고|고시|공지|훈령)\s*(?:제\s*)?\d{2,4}\s*[-–—]\s*\d+\s*호\s*")
_COVER_LABEL = re.compile(r"^[가-힣]{1,12}\s+표\s?지\s+")     # '표지' 가 한 낱말일 때만('발표지원 …' 은 아니다)             # '사업계획서 표지 …'


def polish_title(t: str) -> str:
    """본문에서 찾은 제목을 다듬는다 — 글머리 기호·문서 번호 머리·낫표·표지 표시·상태 꼬리."""
    s = _PRIVATE.sub(" ", t or "")
    m = _DOC_NUMBER_HEAD.match(s)
    if m and _meaningful(s[m.end():]):
        s = s[m.end():]
    m = _COVER_LABEL.match(s)
    # '사업계획서 표지 …' — 앞 낱말이 서류 이름일 때만 표지 표시다('붙임 표지 디자인 …' 은 아니다)
    if m and _meaningful(s[m.end():]) and complete_title(s[: m.end()].split()[0]):
        s = s[m.end():]
    s = _QUOTE.sub(" ", s)
    # 끝에 붙은 밑줄 식별자('lt_pdoc_2022_2') — 시스템이 붙인 부호이지 이름이 아니다
    m = re.search(r"\s+[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\s*$", s)
    if m and _meaningful(s[: m.start()]):
        s = s[: m.start()]
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
                # 괄호로 감싼 줄은 부제다(제목의 뒷줄이 아니다).
                # 판독 결과는 제목을 두 번 적기도 한다 — 같은 제목으로 시작하는 줄은 뒷줄이 아니다
                if re.match(r"^\s*[(（\[]", nxt) or _squash(nxt).startswith(_squash(ln)):
                    break
                if len(joined) + 1 + len(nxt) > 90:
                    break
                if repeats(f"{joined} {nxt}") and not repeats(joined):
                    break                      # 이으면 같은 연도·낱말이 되풀이된다 — 다른 줄(표 머리·부제)이다
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


# 광역 시·도 — 행정구역 이름(일반 지명 사전). 파일 이름의 지역을 본문 제목이 잃으면 다른 지역 문서와 같은 이름이 된다
_REGIONS = (("서울", "서울특별시"), ("부산", "부산광역시"), ("대구", "대구광역시"), ("인천", "인천광역시"), ("광주", "광주광역시"),
            ("대전", "대전광역시"), ("울산", "울산광역시"), ("세종", "세종특별자치시"), ("경기", "경기도"), ("강원", "강원특별자치도"),
            ("충북", "충청북도"), ("충남", "충청남도"), ("전북", "전북특별자치도", "전라북도"), ("전남", "전라남도"),
            ("경북", "경상북도"), ("경남", "경상남도"), ("제주", "제주특별자치도"))
_ORG_WORD = re.compile(r"[가-힣]{2,}(?:대학교|대학|학교|재단|연구원|교육청|[시도군구]청)")
_YEAR_RX = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)|'(\d{2})\s*(?:년|학년도)")
_ROUND_RX = re.compile(r"(\d{1,2})\s*(차\s*(?:년도|연도)|차|단계|주기)")


def _latin(s: str) -> set[str]:
    norm = (s or "").replace("＋", "+")
    return {t.upper().replace(".", "") for t in re.findall(r"[A-Za-z][A-Za-z0-9+.]*", norm) if len(t) >= 2}


def _latin_flat(s: str) -> str:
    return re.sub(r"[\s.]", "", (s or "").replace("＋", "+")).upper()


def _years(s: str) -> set[str]:
    return {a or "20" + b for a, b in _YEAR_RX.findall(s or "")}


def _rounds(s: str) -> set[str]:
    return {n + re.sub(r"\s|연도|년도", "", k) for n, k in _ROUND_RX.findall(s or "")}


def lost_tokens(title: str, fname: str, programs=()) -> list[str]:
    """본문 제목이 파일 이름에서 잃는 구별 정보 — 연도·차수, 지역, 기관, 라틴 약어(사업 이름), 사업 이름, 서류 갈래.

    하나라도 잃으면 본문 제목은 파일 이름보다 덜 구별된다(같은 이름의 문서가 생기거나 갈래가 바뀐다)."""
    from zzaimy.app.doc_routing import guess_kind

    lost: list[str] = []
    tq, fq = _squash(title), _squash(fname)
    lost += [y for y in _years(fname) if y not in _years(title) and y not in tq]
    lost += [r for r in _rounds(fname) if r not in _rounds(title)]
    for names in _REGIONS:
        if any(n in fq for n in names) and not any(n in tq for n in names):
            lost.append(names[0])
    lost += [w for w in _ORG_WORD.findall(fname) if w not in tq]
    tl = _latin_flat(title)
    lost += [t for t in _latin(fname) if t not in tl]
    for p in programs or ():
        pq = _squash(p)
        if len(pq) >= 4 and pq in fq and pq not in tq:
            lost.append(p)
    fk, tk = guess_kind(fname, ""), guess_kind(title, "")
    if fk[0] and fk[0] != tk[0]:
        lost.append(f"갈래:{fk[0]}→{tk[0] or '없음'}")
    return lost


def body_beats_file(title: str, fname: str, programs=()) -> bool:
    """본문 제목을 파일 이름 대신 쓸지.

    파일 이름이 빈약하면 쓴다. 파일 이름이 뜻을 가지면 본문 제목이 완결된 서류 이름(서류 갈래 낱말로 끝남)이고,
    파일 이름의 구별 정보(lost_tokens: 연도·차수·지역·기관·약어·사업 이름·서류 갈래)를 하나도 잃지 않고,
    파일 이름과 같은 문서임을 보일 때만 쓴다 — 세 글자 이상 낱말 하나를 함께 갖거나, 파일 이름 낱말의 삼분의 일 이상을 담을 때.
    본문 제목이 파일 이름의 일부만 잡은 것(제목 뒷줄만 잡힘)이면 더 온전한 파일 이름을 둔다.
    """
    if poor_name(fname) or not re.search(r"[가-힣]", fname):
        return True                     # 한글이 없는 파일 이름('test.pdf', 'law03')은 사람이 붙인 이름이 아니다
    if not complete_title(title) or lost_tokens(title, fname, programs):
        return False
    tq, fq = _squash(title), _squash(fname)
    if tq and tq in fq and len(tq) < len(fq):
        return False                                              # 파일 이름이 본문 제목을 다 담고 더 길다
    # 내용 낱말 — 파일 이름의 한글 낱말 중 서류 갈래 낱말('확인서'·'계획서')과 지역 이름을 뺀 것. 본문 제목이 그 절반 이상을
    # 담아야 같은 문서의 이름이다(갈래 낱말만 겹치면 여러 서식이 묶인 파일의 첫 서식 이름일 수 있고, 지역만 겹치면 표지 구호일 수 있다)
    regions = {n for names in _REGIONS for n in names}
    content = [w for w in _words(fname) if re.search(r"[가-힣]", w) and not complete_title(w) and w not in regions]
    if not content:
        # 파일 이름이 서류 갈래 낱말뿐('가나수익명세서')이면 본문 제목이 그 낱말을 담을 때만
        return all(w in tq for w in _words(fname) if re.search(r"[가-힣]", w))
    hit = [w for w in content if w in tq]
    return len(hit) * 2 >= len(content)


# 뜻이 없는 짧은 파일 이름 — 대체 이름 끝에 붙이지 않는다
_GENERIC_SHORT = frozenset({"문서", "자료", "파일", "스캔", "사본", "첨부", "새 문서", "새문서", "제목", "무제", "기타", "복사본", "이미지",
                            "사진", "그림", "출력", "인쇄", "다운로드", "제목 없음"})


def meta_name(meta: dict | None) -> str:
    """「<서류 갈래> · <사업> <연도>」 — 이미 있는 정보(갈래·사업 분류·연도)로만 만든다."""
    meta = meta or {}
    kind = (meta.get("kind_label") or "").strip()
    prog = (meta.get("program") or "").strip()
    year = str(meta.get("year") or "").strip()
    if not kind and not prog:
        return ""
    tail = " ".join(x for x in (prog, year) if x)
    return " · ".join(x for x in (kind, tail) if x)          # 갈래를 모르면 '문서 ·' 같은 빈말을 붙이지 않는다


# 공문의 머리 — 「수신(자)」 칸 뒤의 「제목」 칸이 그 공문의 이름이다(행정 공문 서식의 일반 꼴). 표로 판독되면 칸이
# 한 줄에 붙어 오고('수신자 (경유) 제목 | …'), 내부 결재 공문은 수신 칸 값 「내부결재」 가 제목 앞에 붙는다
_MEMO_RECIPIENT = re.compile(r"^수\s?신(?:\s?자)?(?:\s|$|[:：|])")
_MEMO_SUBJECT = re.compile(r"(?:^|[\s|])제\s?목\s*[:：|]?\s*(?=\S)(.+)$")


def memo_subject(text: str) -> str | None:
    """공문 「제목」 칸의 글 — 수신 칸이 먼저 나온 문서에서만. 없으면 None."""
    seen = False
    for raw in (text or "").replace("\r", "\n").split("\n")[: _HEAD * 2]:
        ln = _plain(raw)
        if not ln:
            continue
        if _MEMO_RECIPIENT.match(ln):
            seen = True
        if not seen:
            continue
        m = _MEMO_SUBJECT.search(ln)
        if not m:
            continue
        subj = re.split(r"\s\|\s", m.group(1).lstrip("|: "), maxsplit=1)[0]
        subj = re.sub(r"^내부\s?결재\s+", "", subj).strip()
        return polish_title(subj) if _meaningful(subj) else None
    return None


CLIP_CHARS = 1         # 파일 이름이 본문 제목의 첫 한글 이만큼을 잃은 꼴이면 잘린 제목으로 본다


def clipped_title(fname: str, title: str) -> bool:
    """파일 이름이 본문 제목의 첫 한글 한 글자를 잃은 꼴인지 — 옮겨 적다 첫 글자를 잃은 파일 이름('남이공대학교 …').

    뒤쪽이 짧은 파일 이름('… 계획' ← '… 계획(안)')이나 앞 낱말이 빠진 이름('기술지도일지' ← '애로기술 지도 일지')은
    잘린 것이 아니라 줄여 붙인 이름일 수 있어 여기서 다루지 않는다."""
    fq, tq = _squash(_FILE_EXT_RX.sub("", fname or "")), _squash(title)
    if len(fq) < 6 or len(tq) - len(fq) != CLIP_CHARS or not tq.endswith(fq):
        return False
    return bool(re.fullmatch(r"[가-힣]+", tq[:CLIP_CHARS]))


_FILE_EXT_RX = re.compile(r"\.(pdf|hwpx?|docx?|xlsx?|pptx?|png|jpe?g|txt|md)$", re.IGNORECASE)


def choose_name(original: str, text: str = "", meta: dict | None = None,
                ask=None, prefer_file: bool = False) -> dict:
    """문서 이름을 고른다 — {name, title, date, source}. source: body · model · file · meta.

    ask 를 주면, 본문 규칙이 제목을 못 찾았고 파일 이름도 빈약할 때 모델에게 본문의 제목 줄을 짚게 한다
    (doc_identity.find_title_by_model — 본문에 있는 줄만 받는다).
    prefer_file: 본문 제목이 다른 문서의 이름과 겹칠 때(파일 이름은 서로 다른데) — 걷어 낸 파일 이름이 빈약하지 않으면 그것.
    """
    fname = clean_filename(original, text or None)
    title, date = body_title(text) if text else (None, None)
    subj = memo_subject(text) if text else None
    if subj:
        subj = drop_person(subj, text) or None
    # 공문 제목은 서류 갈래 낱말 없이도('… 확인 협조') 그 문서의 이름이다 — 나머지 모양 관문은 그대로
    if subj and shape_problem(subj) in (None, "서류 낱말 없음") and not (prefer_file and not poor_name(fname)) \
            and (poor_name(fname) or body_beats_file(subj, fname, (meta or {}).get("programs") or ())):
        return {"name": subj, "title": subj, "date": date, "source": "body"}
    if title:
        title = drop_person(title, text) or None           # 본문 제목에도 사람 이름은 싣지 않는다
    if title and shape_problem(title):
        title, date = None, None
    # 파일 이름이 본문 제목에서 앞뒤 한두 글자만 떨어진 꼴이면('남이공대학교 …' ← '영남이공대학교 …') 잘린 제목이다 —
    # 본문 제목과 겹치지 않게 하려고 잘린 이름을 쓸 까닭이 없다(구별 정보가 더 없다). 날짜가 붙어 이름은 따로 선다
    clipped = bool(title) and clipped_title(fname, title)
    if title and (clipped or not (prefer_file and not poor_name(fname))) \
            and body_beats_file(title, fname, (meta or {}).get("programs") or ()):
        return {"name": title, "title": title, "date": date, "source": "body"}
    if not title and ask is not None and poor_name(fname) and text:
        from zzaimy.app.doc_identity import find_title_by_model

        got = find_title_by_model(text, ask)
        got = tidy_name(got) if got else None
        if got and not title_problem(got, text) and not shape_problem(got):
            return {"name": got, "title": got, "date": None, "source": "model"}
    if not poor_name(fname):
        return {"name": fname, "title": None, "date": None, "source": "file"}
    alt = meta_name(meta)
    if alt:
        # 짧은 파일 이름('호텔'·'화공'·'3전기')은 학과·부서 같은 단위다 — 뜻이 있으면 끝에 붙여 남긴다
        unit = re.sub(r"^[\d\s.\-_]+", "", fname).strip()
        bare = re.sub(r"\d+\s*(?:번|차|호|쪽|년|월|일|시|분)|오전|오후", "", unit)     # 날짜·시각 낱말은 단위가 아니다
        if len(re.findall(r"[가-힣]", bare)) >= 2 and unit not in _GENERIC_SHORT and unit not in alt:
            alt = f"{alt} · {unit}"
        return {"name": alt, "title": None, "date": None, "source": "meta"}
    return {"name": fname, "title": None, "date": None, "source": "file"}


def naming_meta(db, doc: dict) -> dict:
    """대체 이름에 쓸 정보 — 서류 갈래(documents.kind), 원본 장부의 사업 이름·연도. 없으면 빈 값."""
    from zzaimy.app.doc_routing import KINDS

    out = {"kind_label": KINDS.get(doc.get("kind") or "", ""), "programs": program_names(db)}
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


_PROGRAMS: dict[str, tuple[float, tuple[str, ...]]] = {}


def program_names(db) -> tuple[str, ...]:
    """원본 장부(archive_files)의 사업 이름 목록 — 10분 동안 기억한다. 장부가 없으면 빈 목록."""
    import time

    key = str(getattr(db, "path", ""))
    hit = _PROGRAMS.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        with db._conn() as conn:
            rows = conn.execute("SELECT DISTINCT program_name FROM archive_files").fetchall()
        names = tuple(sorted({str(r[0]) for r in rows if r[0] and str(r[0]) not in ("사업 미분류", "미분류")}))
    except Exception:
        names = ()
    _PROGRAMS[key] = (time.time(), names)
    return names
