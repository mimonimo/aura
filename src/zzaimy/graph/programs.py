"""사업 분류 — 사업 카드와 문서별 사업·연차·갈래(ADR-0048 0항).

사업 이름은 코드에 넣지 않는다. 문서 제목·표지·앞머리에서 사업명(…지원사업·…육성사업 등)과 괄호 약칭(…(LINC 3.0))을 뽑고,
한 문서 안에서 '긴 이름(약칭)'·'약칭(긴 이름)'으로 함께 쓰인 표기를 같은 사업으로 묶는다(다른 이름은 문서가 알려 준다).
문서는 제목(무게 3)·앞머리(무게 1) 언급으로 점수를 매겨 사업을 고르고, 근거가 약하거나 엇갈리면 검토 대기로 둔다.
폴더 경로를 알면(DGX 원본은 사업별 폴더) 경로 언급도 무게 3 으로 더한다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from zzaimy.graph.entities import _GENERIC, _PROGRAM, acronyms, clean_title, program_key

# 소수점 판번호가 붙은 영문 약칭(LINC 3.0·LINC3.0) — 사업명 옆 괄호가 없어도 제목에 홀로 쓰인다. 'Track7' 같은 번호 매김은 아니다
_VERSIONED = re.compile(r"(?<![A-Za-z])([A-Z]{2,}[A-Za-z+]{0,8})\s?(\d\.\d)(?![\d.])")
_PAIR = re.compile(r"([가-힣A-Za-z0-9·()+ ]{4,40}?(?:사업|사업단))\s*\(([A-Za-z][A-Za-z0-9+. ]{1,14})\)")
# 띄어 쓴 사업명 — 'AID(AI+Digital) 전환 중점 전문대학 지원사업'. 낱말 2~7개가 …사업으로 끝난다
_SPACED = re.compile(r"((?:[가-힣A-Za-z0-9()·+]+ ){1,6}[가-힣A-Za-z0-9()·+]*(?:지원|육성|혁신|선도|중점)?사업)(?![가-힣])")   # 한 줄 안에서만
_LEAD_DROP = re.compile(r"^(?:\d{6}_?|(?:19|20)\d{2}(?:학년도|년도|년)?|학년도|년도|제?\d+(?:차|단계)?|[가-힣]*[은는이가을를의에와과및]|및|등|위한|대한|관한|따른)$")


def _trim_name(name: str) -> str:
    """사업명 앞에 붙은 연도·조사로 끝나는 말·접속어를 뗀다('본인 및 참여 인력은 3단계 …사업' → '3단계 …사업')."""
    toks = name.split()
    cut = 0
    for i, t in enumerate(toks[:-1]):
        if _LEAD_DROP.match(t) and not re.match(r"^\d+단계$", t):
            cut = i + 1
    out = " ".join(toks[cut:]).strip()
    return out if len(re.sub(r"\s", "", out)) >= 6 else ""
# 약칭은 영문으로 시작한다 — 괄호 안 한글(기관명 「(영남이공대학교)」)·숫자(「(2025)」)는 약칭이 아니다
_LATIN_ACR = re.compile(r"\(([A-Za-z][A-Za-z0-9+.]{1,12})\)")
# 이름 맨 앞의 괄호 꼬리표 — 「(영남이공대학교)3단계 …」·「(내용추가) …」 — 이름의 일부가 아니다
_LEAD_LABEL = re.compile(r"^\s*\([^)]{1,20}\)\s*")
# 때를 가리키는 말 — 이것만 남는 이름(「1차년도(2025) 사업」)은 사업명이 아니다
_TIME_WORDS = re.compile(r"[1-9]\s*차\s*년도|\(?\s*(?:19|20)\d{2}\s*(?:학년도|년도|년)?\s*~?\s*\)?|학년도|년도|\d+\s*개|\d+\s*단계")
# 「대구 RISE사업」·「RISE 사업」 — 앞말 하나 + 영문 약칭 + 사업: 그 약칭의 사업이다
_BARE_ACR = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9+]{2,10})\s?사업(?![가-힣])")
_ACR_NAME = re.compile(r"^(?:[가-힣]{2,10}\s*)?([A-Z][A-Za-z0-9+.]{1,10})\s*사업$")


def _is_acr(a: str) -> bool:
    """약칭다운가 — 영문으로 시작, 소문자 풀이(AI+Digital) 아님, 판 표기(ver.4·v2.0) 아님."""
    a = (a or "").strip()
    return bool(re.match(r"[A-Za-z]", a)) and not re.search(r"[a-z]{3}", a) and not re.match(r"(?i)^v(?:er)?[\s._]*\d", a)


_KOR_PAREN = re.compile(r"(?<=[가-힣\s])\(([가-힣]{2,4})\)")
# 이름이 바뀌었음을 알리는 표기 — 「RISE(現 앵커)」·「앵커 추진방안(RISE 재구조화)」·「명칭 변경」
# 「현」 한 글자는 「현황」 같은 낱말에도 있다 — 「現」 또는 괄호 안의 「(현 X)」만
_RENAMED = re.compile(r"現\s*[가-힣A-Za-z]|\(\s*현\s+[가-힣A-Za-z]|재구조화|명칭\s*(?:을\s*)?변경")


def _name_ok(name: str) -> bool:
    core = re.sub(r"[\s()·]", "", _TIME_WORDS.sub("", name)).replace("사업", "")
    return len(re.findall(r"[가-힣A-Za-z]", core)) >= 4 and not fragment_name(name)


# ── 문장 조각 거르기 ────────────────────────────────────────────────────────
# 사업 이름은 명사구다. 본문 문장에서 「…사업」 앞까지를 잘라 온 조각(「아울러 2017년부터 …사업」「따라서 LINC+사업」)은
# 이름이 아니다 — 특정 사업이 아니라 문장의 꼴(길이·문장 부호·접속 부사·토씨·어미)로 가른다.
NAME_MAX = 40                       # 띄어쓰기를 뺀 글자 수 — 이보다 긴 「이름」은 문장이다
NAME_MAX_WORDS = 8
# 문장을 여는 접속 부사·지시어 — 이름의 첫 낱말이 될 수 없다
_OPENERS = re.compile(r"^(?:아울러|따라서|또한|또|그리고|그러나|하지만|그러므로|그래서|특히|이에|즉|한편|나아가|더불어|더욱이|다만|단|"
                      r"이를|이와|이러한|이같은|이번|금번|상기|위|본|각|동|해당|기존의?|향후|앞으로|현재|최근)$")
# 낱말 끝의 토씨·어미 — 이름 가운데 낱말이 이렇게 끝나면 문장이 이어지는 중이다(「2017년부터」「추진하여」「대학에서」)
_INNER_END = re.compile(r"(?:부터|까지|에서|에게|으로|로서|로써|하여|하고|하며|되어|되며|해서|하는|되는|하던|되던|했던|됐던|"
                        r"위해|통해|위한|통한|대한|관한|따른|않은|있는|없는|이며|이고|였던|었던|으며|면서|지만|는데|"
                        r"[가-힣]{2,}(?:는|를|은))$")
# 문장 부호 — 쉼표·마침표(판 번호 「3.0」은 빼고)·물음표·따옴표·쌍점
_SENT_PUNCT = re.compile(r"[,;:!?。“”\"'‘’…]|\.(?!\d)|(?<!\d)\.")


def fragment_name(name: str) -> bool:
    """사업 이름이 아니라 문장 조각인가 — 길이(띄어쓰기 뺀 NAME_MAX 자·낱말 NAME_MAX_WORDS 개 초과), 문장 부호,
    첫 낱말이 접속 부사·지시어, 마지막이 아닌 낱말이 토씨·어미로 끝남. 처음 보는 사업 이름에도 같은 판정이 나도록 꼴만 본다."""
    s = " ".join((name or "").split())
    if not s:
        return True
    if len(s.replace(" ", "")) > NAME_MAX or len(s.split(" ")) > NAME_MAX_WORDS:
        return True
    if _SENT_PUNCT.search(s):
        return True
    toks = s.split(" ")
    if len(toks) > 1 and _OPENERS.match(toks[0]):
        return True
    return any(_INNER_END.search(re.sub(r"[()]+$", "", t)) for t in toks[:-1])


# 이름 앞이 잘린 꼴 — 글 속 「\n」 표기의 n 이 붙은 것(「n의과대학 …」), 앞 낱말이 줄바꿈에 잘려 한 글자만 남은 것(「연 공동기술개발 …」)
_LEAD_CUT = re.compile(r"^(?:[a-z](?=[가-힣])|[가-힣]\s+(?=[가-힣A-Za-z]{2}))")


def clean_label(name: str) -> str:
    """보일 사업 이름의 앞 잘림을 다듬는다 — 글 속 줄바꿈 표기의 영문자, 한 글자만 남은 앞 낱말. 이미 지은 그래프 이름에도 쓴다."""
    s = " ".join((name or "").replace("\\n", " ").replace("\\t", " ").split())
    s = re.sub(r"^[\s·ㆍ•∙\-–—*+,.:;○●□■▶▷※]+", "", s)           # 앞 글머리·문장 부호(「. RISE 비전」)
    # 앞이 잘려 닫는 괄호부터 시작하는 조각(「) 3단계 …」), 번호 꼬리표(「01) …」), 가린 연도 자리표시(「0000년 …」)
    s = re.sub(r"^(?:\d{1,3}\s*)?\)\s*", "", s)
    s = re.sub(r"^0{2,4}\s*(?:학년도|년도|년)\s*", "", s)
    # 숫자가 잘려 단위만 남은 앞말(「2025학년도」의 「학년도」, 「2025년」의 「년」) — 「년」 한 글자는 아래 한 글자 규칙이 뗀다
    out = re.sub(r"^(?:학년도|년도)\s+(?=\S)", "", s)
    out = _LEAD_CUT.sub("", out).strip()
    return out if len(out.replace(" ", "")) >= 4 else s


def _same_acr(a: str, b: str) -> bool:
    """기호만 다른 약칭(「LINC」=「LINC+」)인가 — 숫자·글자가 더 붙은 것(「LINC30」)은 다른 사업일 수 있어 같지 않다."""
    if a == b:
        return True
    lo, hi = sorted((a, b), key=len)
    return hi.startswith(lo) and not re.search(r"[A-Z0-9]", hi[len(lo):])


def _one_edit(a: str, b: str) -> bool:
    """글자 하나만 바꾸거나 빼거나 더한 사이인가(오기 판정)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    lo, hi = sorted((a, b), key=len)
    return any(hi[:i] + hi[i + 1:] == lo for i in range(len(hi)))


def _id_acr(node_id: str) -> set[str]:
    tail = (node_id or "").split(":", 1)[-1]
    return {_acr(tail)} if re.fullmatch(r"[0-9a-z+.]{2,12}", tail) and re.search(r"[a-z]", tail) else set()


def alias_filters(prog: dict, others: list[dict], ledger: dict | None = None) -> tuple[set[str], set[str], set[str]]:
    """별칭 거르기 재료 (정체 약칭, 남의 약칭, 장부가 인정한 표기) — 모두 _acr 꼴.

    - 정체 약칭: 사업 이름(label)에 든 약칭과 id 꼬리, 그리고 그와 기호만 다른 자기 약칭(LINC → LINC+). 카드에 섞여 들어온 약칭
      목록 전체를 정체로 보지 않는다 — 그러면 잘못 붙은 남의 약칭이 「자기 것」이 되어 걸러지지 않는다
    - 남의 약칭: 다른 사업 노드의 이름·id·약칭 목록에 있는 약칭 가운데 정체 약칭과 같지 않은 것
    - 장부 표기: 외부 확인 장부(kg_external.json)의 사업 이름·terms — 오기처럼 보여도 출처가 있으면 둔다"""
    def label_acrs(p: dict) -> set[str]:
        return {_acr(t) for t in _ACR_TOKEN.findall(str(p.get("label") or ""))} | _id_acr(p.get("id") or "")
    canon = label_acrs(prog)
    own = {_acr(a) for a in ((prog.get("props") or {}).get("acronyms") or [])}
    canon |= {a for a in own if any(_same_acr(a, c) for c in canon)}
    foreign: set[str] = set()
    for o in others:
        foreign |= label_acrs(o) | {_acr(a) for a in ((o.get("props") or {}).get("acronyms") or [])}
    foreign = {f for f in foreign if f and not any(_same_acr(f, c) for c in canon)}
    # 장부: 이 사업 항목(정체 약칭이나 이름이 맞는 항목)의 표기는 인정, 다른 사업 항목의 약칭은 남의 약칭
    allowed: set[str] = set()
    label_flat = re.sub(_FLAT, "", str(prog.get("label") or "")).upper()
    for e in (ledger or {}).get("programs", []) or []:
        if not e.get("sources"):
            continue
        terms = [str(t) for t in (e.get("terms") or []) + [e.get("name") or ""] + (e.get("acronyms") or []) if t]
        acrs = set()
        for t in terms:
            acrs |= {_acr(x) for x in _ACR_TOKEN.findall(t)} | ({_acr(t)} if _is_acr(t) else set())
        mine = any(any(_same_acr(a, c) for c in canon) for a in acrs) or \
            any(re.sub(_FLAT, "", t).upper() == label_flat for t in terms if t)
        if mine:
            allowed |= acrs
        elif e.get("category") != "사업 아님":
            foreign |= {a for a in acrs if not any(_same_acr(a, c) for c in canon)}
    return canon, foreign - allowed, allowed


def alias_list(names, acronyms=(), limit: int = 8, foreign: set[str] | frozenset = frozenset(),
               canonical: set[str] | frozenset = frozenset(), allowed: set[str] | frozenset = frozenset()) -> tuple[list[str], int]:
    """화면에 보일 다른 이름 — 문장 조각을 거르고, 보일 이름 규칙(program_names.display_name)으로 다듬어 띄어쓰기만 다른 것은
    하나로, 약칭 먼저·짧은 이름 순으로 limit 개.
    - foreign(남의 약칭, alias_filters)이 낱말로 든 이름은 남의 사업이라 뺀다
    - canonical(정체 약칭)과 한 글자만 다른 약칭(「LNC+」「LING+」)은 오기로 보고 뺀다 — allowed(장부 표기)에 있으면 둔다
    돌려주는 것: (보일 이름들, 더 있는 수)."""
    from zzaimy.app.program_names import display_name

    seen, out = set(), []
    pool = [a for a in acronyms or [] if a] + sorted((n for n in names or [] if n), key=lambda n: len(n))
    for n in pool:
        n = display_name(clean_label(n)) or clean_label(n)
        k = re.sub(r"[\s.·ㆍ\-_()]+", "", n).upper()
        if not k or k in seen or fragment_name(n):
            continue
        toks = {_acr(t) for t in _ACR_TOKEN.findall(n)} | ({_acr(n)} if _is_acr(n) else set())
        if foreign and toks & set(foreign):
            continue
        if canonical and any(t not in canonical and t not in allowed and not any(_same_acr(t, c) for c in canonical)
                             and any(_one_edit(t, c) for c in canonical) for t in toks):
            continue
        seen.add(k)
        out.append(n)
    return out[:limit], max(len(out) - limit, 0)


# 이름 앞 글머리표·가운뎃점 — 「· 전문대학혁신지원사업」은 목록 줄의 글머리가 붙은 것
_LEAD_PUNCT = re.compile(r"^[\s·ㆍ•∙\-–—*,.:;○●□■▶▷※]+")


# 사업 이름 앞의 부처·표 머리말 — 「교육부 초광역 성장엔진 인재육성 사업」「구분 대경권 사업」의 교육부·구분은 이름이 아니다
_LEAD_ORG = re.compile(r"^(?:(?:교육부|고용노동부|산업통상자원부|과학기술정보통신부|행정안전부|중소벤처기업부|보건복지부|문화체육관광부|"
                       r"국토교통부|농림축산식품부|여성가족부|해양수산부|환경부|교육과학기술부|구분|항목|사업명|과제명|사업구분|분야|"
                       r"[가-힣]{2,12}대학교)\s+)+")      # 기관 이름 「○○대학교」만 — 「전문대학 혁신지원사업」의 전문대학은 이름이다
# 고유한 낱말 없이 일반어만으로 된 이름(「재정지원 사업」「지자체 연계 사업」) — 사업 하나를 가리키지 않는다
_GENERIC_WORDS = re.compile(r"사업단?|지원|육성|운영|활성화|재정|국고|정부|교육부|지자체|연계|대학|전문대학|기본|일반|공통|기타|및|등|의|"
                            r"당해|연도|년도|해당|금년|올해|본|각종|관련|"
                            r"보조금|복지|창업|환경개선|동아리|외부|용역|자료개발|활동|프로그램|교육|"
                            r"대구광역시|대구시|대구|경상북도|경북|지역|"
                            r"[0-9]+|[()·\s\-+.,]")


def vague_name(name: str) -> bool:
    return len(_GENERIC_WORDS.sub("", name or "")) < 2


def _norm_name(name: str) -> str:
    n = _LEAD_ORG.sub("", _LEAD_PUNCT.sub("", _LEAD_LABEL.sub("", name or "")).strip()).strip()
    # 앞에 붙은 때·순번 꼬리표 — 「8월 RISE사업」·「3(경대) RISE사업」·「25재정지원사업」의 8월·3(경대)·25
    # 숫자 뒤가 「단계·차·주기·기」면 이름의 일부다(「3단계 산학연협력 …」의 3) — 떼지 않는다
    n = re.sub(r"^(?:(?:19|20)?\d{2}\s*(?:학년도|년도|년)|\d{1,2}\s*월|\d{1,2}\s*\([^)]{1,10}\)|\d{1,4}(?!\s*(?:단계|차|주기|기|학년도|년도|년)))\s*(?=[가-힣A-Za-z])", "", n).strip()
    # 번호·붙임 꼬리표 — 「51) 사회맞춤형LINC 육성사업」「2) LINC+ 사업」「붙임3 LINC+사업」
    n = re.sub(r"^(?:\d{1,3}\)|붙임\s*\d*[.)]?)\s*(?=[가-힣A-Za-z])", "", n).strip()
    # 날짜 꼬리표 — 「190401(혁신지원사업」「20240315_RISE사업」의 날짜와 뒤의 괄호·밑줄
    n = re.sub(r"^\d{6,8}\s*[(_\-\s]\s*(?=[가-힣A-Za-z])", "", n).strip()
    # 닫히지 않은 여는 괄호 — 「보도자료(COSS 혁신융합대학사업」은 파일 이름의 앞부분이 붙은 것. 괄호 뒤가 사업명이다
    if n.count("(") > n.count(")"):
        tail = n[n.rfind("(") + 1:].strip()
        if len(tail) >= 4:
            n = tail
    return clean_label(n)


_ROUND = re.compile(r"([1-9])\s*차\s*년도")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:년|학년도|\.)")


def plausible_year(y) -> bool:
    """사업 문서가 다룰 수 있는 연도인가 — 본문의 「1983년 설립」 같은 연혁 숫자를 문서 연도로 쓰지 않는다."""
    import datetime
    try:
        return 2000 <= int(y) <= datetime.date.today().year + 2
    except (TypeError, ValueError):
        return False


def _first_year(*texts: str) -> int | None:
    for t in texts:
        for m in _YEAR.finditer(t or ""):
            if plausible_year(m.group(1)):
                return int(m.group(1))
    return None



# 본문의 연도 표기 — 「2023학년도」「2024년도」「2025년」(뒤에 「N월」이 오는 날짜는 뺀다)
_BODY_YEAR = re.compile(r"(?<![\d.])((?:19|20)\d{2})\s*(?:학년도|년도|년)(?!\s*\d{1,2}\s*월)")
# 연혁 표기 — 기관·기업의 설립 연도는 사업 수행 연도가 아니다(「1968년 개교」「2010년 3월 설립」「연혁: 2005년 …」)
_HISTORY_AFTER = re.compile(r"^[^.\n|]{0,14}?(?:설립|개교|창립|개원|개관|창업|연혁|이래)")
_HISTORY_BEFORE = re.compile(r"(?:연혁|설립|개교|창립|개원|창업)\s*[:：]?\s*$")


def body_year(text: str, pattern: re.Pattern | None = None) -> int | None:
    """본문(첫머리)에서 사업 수행 연도로 쓸 만한 첫 연도 — 경로·파일 이름 연도보다 약한 근거라 걸러서 쓴다.

    - 있을 법한 범위(plausible_year, 2000년 이후)만 — 「1968년」 같은 연혁 숫자는 수행 연도가 아니다
    - 설립·개교·창립·개원·연혁 같은 연혁 표기에 붙은 연도는 건너뛴다
    특정 기관·사업 이름이 아니라 표기 꼴로만 판단한다."""
    t = text or ""
    for m in (pattern or _BODY_YEAR).finditer(t):
        if not plausible_year(m.group(1)):
            continue
        if _HISTORY_AFTER.search(t[m.end():m.end() + 24]) or _HISTORY_BEFORE.search(t[max(0, m.start() - 12):m.start()]):
            continue
        return int(m.group(1))
    return None


_EVAL_RESULT = re.compile(r"평가\s*(?:결과|의견)|종합\s*의견")
HEAD_CHARS = 4000


_FLAT = r"[\s._\-]+"          # 대조할 때 지우는 띄어쓰기·점·밑줄·붙임표 — 파일 이름은 띄어쓰기 대신 밑줄을 쓴다


def _acr(s: str) -> str:
    return re.sub(_FLAT, "", s or "").upper()


@dataclass
class ProgramCard:
    key: str                                   # 대표 키(program_key)
    names: Counter = field(default_factory=Counter)
    acrs: Counter = field(default_factory=Counter)
    renamed: list[str] = field(default_factory=list)      # 이름이 바뀐 같은 사업이라는 문서 근거(제목)
    display: str = ""                                      # 외부 확인 장부의 정식 이름(있으면 표시 이름으로)
    not_program: bool = False                              # 외부 확인으로 사업이 아님(조사·평가 등) — 그래프 사업 노드를 만들지 않는다
    fixed_id: str = ""                                     # 정리 전 id 를 고정(보관 묶음·배정 기록이 사업 id 를 쓴다)

    @property
    def name(self) -> str:
        if self.display:
            return self.display
        raw = self.names.most_common(1)[0][0] if self.names else (self.acrs.most_common(1)[0][0] if self.acrs else self.key)
        return _LEAD_PUNCT.sub("", raw) or raw

    @property
    def node_id(self) -> str:
        if self.fixed_id:
            return self.fixed_id
        a = self.acrs.most_common(1)[0][0] if self.acrs else ""
        return "program:" + (re.sub(r"[^0-9a-z가-힣]+", "", a.lower()) or self.key)

    def surfaces(self) -> set[str]:
        return {re.sub(_FLAT, "", n) for n in self.names} | {_acr(a) for a in self.acrs}


_ACR_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9+]{1,10}(?:\.\d+)?(?:\s?\d\.\d)?)(?![a-z0-9.])")


def lists_other_acronym(long_: str, short: str) -> bool:
    """긴 이름 안에 짝 약칭과 다른 영문 약칭이 또 있는가 — 「LINC+ 및 대학 자율역량 강화(ACE)」처럼 사업 둘을 늘어놓은 구절이면
    그 구절을 근거로 두 약칭을 한 사업으로 잇지 않는다. 판 표기·앞부분만 다른 것(LINC ⊂ LINC3.0, AI ⊂ AID)은 같은 약칭으로 본다."""
    me = _acr(short)
    for t in _ACR_TOKEN.findall(long_ or ""):
        o = _acr(t)
        if o and o != me and not o.startswith(me) and not me.startswith(o):
            return True
    return False


def _mentions(text: str) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(사업명들, 약칭들, (긴 이름, 약칭) 짝들)."""
    text = (text or "").replace("\\n", "\n").replace("\\t", " ")      # 글 속 「\\n」 표기는 줄바꿈 — 이름에 n 이 붙지 않게
    names = [_norm_name(m.group(0)) for m in _PROGRAM.finditer(text or "") if m.group(0) not in _GENERIC]
    names += [_norm_name(t) for t in (_trim_name(m.group(1)) for m in _SPACED.finditer(text or "")) if t and t not in _GENERIC]
    # 띄어 쓴 가운뎃점·쉼표·빗금은 사업 둘을 늘어놓은 것이다(「…(COSS)사업 · …(HiVE)사업」) — 한 이름으로 묶지 않는다
    names = [_norm_name(part) for n in names for part in re.split(r"\s+[·,/]\s+", n) if part.endswith("사업")]
    names = [n for n in names if n and n not in _GENERIC and _name_ok(n)]
    pairs = [(t, m.group(2).strip()) for m in _PAIR.finditer(text or "")
             if (t := _norm_name(_trim_name(m.group(1).strip()))) and _name_ok(t) and _is_acr(m.group(2))
             and not lists_other_acronym(t, m.group(2))]
    acrs = [f"{m.group(1)}{m.group(2)}" for m in _VERSIONED.finditer(text or "")]
    acrs += _BARE_ACR.findall(text or "")                     # 「RISE사업(2025~)」 — 홀로 쓰인 약칭+사업
    for n in names:
        # 괄호 속 한글 약칭 후보(「지역성장 인재양성체계(앵커)사업」의 앵커) — build_cards 가 다른 제목에서 「앵커사업」으로도 쓰이는지 본다
        for k in _KOR_PAREN.findall(n):
            pairs.append((n, "k:" + k))
        inner = [a for a in _LATIN_ACR.findall(n) if _is_acr(a)]          # 「(AI+Digital)」은 풀이, 「(ver.4)」는 판 표기
        acrs += inner
        # 「지역혁신중심 대학지원체계(RISE)사업」 — 이름 속 약칭과 같은 사업. 이름에 다른 약칭이 또 있으면(사업을 늘어놓은 구절) 잇지 않는다
        pairs += [(n, a) for a in inner if not lists_other_acronym(n, a)]
        m = _ACR_NAME.match(n)
        if m:
            acrs.append(m.group(1))
            if not lists_other_acronym(n, m.group(1)):
                pairs.append((n, m.group(1)))                 # 「대구 RISE사업」 — 그 약칭의 사업
    return names, acrs, pairs


RENAME_NAME_MIN = 6        # 이름 바뀜 근거로 쓰는 사업 이름 표기의 최소 글자 수(띄어쓰기 뺀 것)


def rename_hits(title: str, keys: list[str]) -> list[str]:
    """이름 바뀜 표기(現·재구조화·명칭 변경)가 든 제목이 부르는 사업 표기들 — build_cards 의 키(「a:약칭」「n:이름」) 가운데

    - 약칭(a:)은 영문·숫자 낱말 경계에서 맞을 때만(「RIS」가 「RISE」 안에서 맞지 않게)
    - 이름(n:)은 RENAME_NAME_MIN 자 이상이고 일반어만으로 되지 않은 것만 — 「재구조화」「인재양성」「지역혁신」 같은 낱말 키가
      제목 글자 속에서 맞아 그 낱말을 품은 다른 사업 묶음을 끌어들이지 않게
    - 맞은 이름이 다른 맞은 이름 속에 들어 있으면 긴 쪽만(「대학지원체계」 ⊂ 「지역혁신중심대학지원체계」)
    특정 사업 이름이 아니라 표기의 꼴로만 고른다."""
    flat_tp = re.sub(_FLAT, "", title or "").upper()
    hits: dict[str, str] = {}
    for k in keys:
        surf = re.sub(_FLAT, "", k[2:]).upper()
        if len(surf) < 2 or surf not in flat_tp:
            continue
        if k.startswith("a:"):
            if re.search(r"[A-Z0-9]", surf) and not re.search(
                    r"(?<![A-Z0-9])" + re.escape(surf) + r"(?![A-Z0-9])", flat_tp):
                continue
        elif len(surf) < RENAME_NAME_MIN or vague_name(k[2:]) or surf.count("(") != surf.count(")"):
            continue
        hits[k] = surf
    names = [s for k, s in hits.items() if k.startswith("n:")]
    return [k for k, s in hits.items() if k.startswith("a:") or not any(s != o and s in o for o in names)]


def build_cards(docs: list[dict]) -> list[ProgramCard]:
    """문서들에서 사업 카드를 만든다. docs = [{id, filename, head, path?}] — 같은 사업의 다른 표기를 문서 속 짝으로 묶는다."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # 파일 이름에 자주(5번 이상) 나오는 영문 약칭은 사업의 정체다 — 서로 다른 정체를 이미 가진 두 묶음은
    # 「다른 이름」 짝만으로 합치지 않는다. 본문 앞머리의 앞 단계 언급(LINC3.0 문서 속 「…대학(LINC+) 육성사업」)이
    # 두 사업을 한 카드로 만들지 않게 한다
    fn_acr: Counter = Counter()
    for d in docs:
        _n, _a, _p = _mentions(clean_title(d.get("filename") or ""))
        fn_acr.update({"a:" + _acr(a) for a in _a})
    strong = {k for k, n in fn_acr.items() if n >= 5}
    ident: dict[str, set] = {}

    def union(a: str, b: str, force: bool = False) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        ia = ident.get(ra, {ra} & strong)
        ib = ident.get(rb, {rb} & strong)
        if not force and ia and ib and ia.isdisjoint(ib):
            return                                     # 서로 다른 정체 — 합치지 않는다
        parent[rb] = ra
        ident[ra] = ia | ib

    names: dict[str, Counter] = defaultdict(Counter)
    acr_seen: dict[str, Counter] = defaultdict(Counter)
    titled: set[str] = set()                    # 파일 이름·경로에 나온 키
    kor_alias: dict[str, set] = defaultdict(set)  # 괄호 속 한글 약칭 → 그 약칭을 품은 긴 이름 키
    title_texts: list[str] = []
    head_docs: dict[str, set] = defaultdict(set)  # 앞머리에만 나온 키 → 문서들
    head_pairs: dict[tuple[str, str], set] = defaultdict(set)   # 앞머리에서만 본 (이름 키, 약칭 키) → 문서들
    for d in docs:
        tp = f"{clean_title(d.get('filename') or '')}\n{d.get('path') or ''}"
        title_texts.append(tp)
        tn, ta, tpairs = _mentions(tp)
        titled.update("n:" + program_key(n) for n in tn)
        titled.update("a:" + _acr(a) for a in ta)
        titled.update("n:" + program_key(l_) for l_, _s in tpairs)
        text = f"{tp}\n{(d.get('head') or '')[:HEAD_CHARS]}"
        ns, acs, pairs = _mentions(text)
        for n in ns:
            head_docs["n:" + program_key(n)].add(d.get("id"))
        for a in acs:
            head_docs["a:" + _acr(a)].add(d.get("id"))
        for n in ns:
            k = "n:" + program_key(n)
            find(k)
            names[k][n.strip()] += 1
        for a in acs:
            k = "a:" + _acr(a)
            find(k)
            acr_seen[k][a.strip()] += 1
        title_pairs = {(l_, s_) for l_, s_ in tpairs}
        doc_title_keys = {"n:" + program_key(n) for n in tn} | {"a:" + _acr(a) for a in ta} | \
            {"n:" + program_key(l_) for l_, _s in tpairs} | {"a:" + _acr(s_) for _l, s_ in tpairs if not s_.startswith("k:")}
        for long_, short in pairs:                              # 문서가 알려 주는 다른 이름
            if short.startswith("k:"):
                kor_alias[short[2:]].add("n:" + program_key(long_))
                continue
            kn, ka = "n:" + program_key(long_), "a:" + _acr(short)
            names[kn][long_] += 1
            acr_seen[ka][short] += 1
            # 제목·경로에서 함께 쓰인 짝, 또는 짝의 한쪽이 그 문서 제목·경로에도 나오면 바로 잇는다. 본문 앞머리에서만 본 짝은
            # 문서 두 건 이상이 같은 짝을 쓸 때만 —
            # 앞머리 한 곳의 우연한 구절이 다른 사업 약칭을 이 사업 별칭으로 붙이지 않게
            if (long_, short) in title_pairs or kn in doc_title_keys or ka in doc_title_keys:
                union(kn, ka)
            else:
                head_pairs[(kn, ka)].add(d.get("id"))
    for (kn, ka), ds in head_pairs.items():
        if len(ds) >= 2:
            union(kn, ka)
    # 글자 자리만 바뀐 약칭(오타 「RSIE」)은 열 배 넘게 흔한 같은 글자의 약칭과 같은 사업으로 본다
    acr_n = {k: sum(c.values()) for k, c in acr_seen.items()}
    for k, n in acr_n.items():
        a = k[2:]
        if len(a) < 4:
            continue
        for k2, n2 in acr_n.items():
            b = k2[2:]
            if k2 != k and n2 >= 10 * n and len(b) == len(a) and sorted(a) == sorted(b) \
                    and sum(x != y for x, y in zip(a, b)) == 2:
                union(k2, k, force=True)
                break
    # 괄호 속 한글 약칭은 다른 제목에서도 「약칭+사업」으로 쓰일 때만 약칭이다(「앵커사업」) — 「(주관)」·「(안)」은 그렇게 안 쓰인다
    joined = "\n".join(title_texts)
    for alias, longs in kor_alias.items():
        if re.search(rf"{re.escape(alias)}\s*사업(?![가-힣]*계획서)", joined) or re.search(rf"{re.escape(alias)}\s*\(", joined):
            ka = "a:" + alias
            acr_seen[ka][alias] += 1
            titled.add(ka)
            for kn in longs:
                union(kn, ka)
            # 「대구 앵커사업」 — 앞말 하나 + 받아들인 약칭 + 사업(영문 약칭의 「대구 RISE사업」과 같은 규칙)
            for k in list(parent):
                if k.startswith("n:") and re.fullmatch(rf"(?:[가-힣]{{2,10}})?{re.escape(alias)}(?:사업)?", k[2:]):
                    union(k, ka)
    # 이름 바뀜 — 한 제목이 두 사업을 함께 말하며 「現·재구조화·명칭 변경」을 쓰면 같은 사업이다(교육부 2026: RISE → 앵커)
    renamed_ev: dict[str, list[str]] = defaultdict(list)
    for tp in title_texts:
        tp = tp.split("\n")[0]                     # 파일 이름만 — 폴더 경로에는 여러 사업이 함께 나온다(「업무공유(LINC사업단)」 아래 LINC+·LINC3.0)
        if not _RENAMED.search(tp):
            continue
        # 묶음은 제 정체로 불릴 때만 센다 — 파일 이름에 자주 나오는 약칭(정체)을 가진 묶음은 그 약칭으로, 정체 약칭이 없는 묶음은
        # 이름·약칭으로. 묶음에 섞여 든 다른 이름·약칭(본문 조각) 하나가 제목에 맞았다고 그 묶음을 이름 바뀜에 끌어들이지 않는다
        roots = set()
        for k in rename_hits(tp, list(parent)):
            r = find(k)
            own = ident.get(r, {r} & strong)
            if not own or k in own:
                roots.add(r)
        if len(roots) == 2:                        # 셋 이상이면 어느 둘이 같은 사업인지 모른다 — 잇지 않는다
            ks = sorted(roots)
            for k in ks[1:]:
                union(ks[0], k, force=True)        # 「現·재구조화·명칭 변경」이 적힌 이름 바뀜 — 정체가 달라도 같은 사업
            renamed_ev[find(ks[0])].append(tp.split("\n")[0][:120])
    # 본문 앞머리 구절 하나가 사업이 되지 않게(「대상으로 사업」·「각종 결재 시 … 해당사업」): 파일 이름·경로에 나오거나
    # 문서 세 건 이상의 앞머리에 나온 표기가 하나라도 있는 묶음만 사업 카드로 둔다
    keep_root: set[str] = set()
    for k in list(parent):
        if k in titled or len(head_docs.get(k, ())) >= 3:
            keep_root.add(find(k))
    groups: dict[str, ProgramCard] = {}
    for k in list(parent):
        if find(k) not in keep_root:
            continue
        root = find(k)
        card = groups.setdefault(root, ProgramCard(key=root.split(":", 1)[1]))
        if not card.renamed and renamed_ev.get(root):
            card.renamed = sorted(set(renamed_ev[root]))[:5]
        if k.startswith("n:"):
            card.names.update(names[k])
        else:
            card.acrs.update(acr_seen[k])
    # 본문 앞머리에만 나온 이름(파일 이름·경로에는 없음)이 정체가 확실한 약칭을 꼭 하나 품으면 그 사업의 다른 표기다 —
    # 「귀하께서 참여한 사회맞춤형 LINC+사업」「매 분기마다 … LINC+ 사업」 같은 문장 조각이 따로 사업 카드가 되어
    # 긴 이름부터 대조하는 분류에서 문서를 가져가지 않게 한다. 약칭이 둘 이상이면(「혁신지원사업 및 LINC+」) 두지 않는다
    titled_roots = {find(k) for k in titled if k in parent}
    strong_root = {a: find(a) for a in strong if a in parent}
    for root in list(groups):
        card = groups[root]
        # 파일 이름 출신이라도 언급이 드문(3번 이하) 조각(「○○○ 교수님 LINC 사업」 같은 개인 이름이 붙은 조각)은 같은 규칙으로 접는다
        if root in titled_roots and sum(card.names.values()) + sum(card.acrs.values()) > 3:
            continue
        flat_su = [re.sub(_FLAT, "", x).upper() for x in card.surfaces()]
        found = {a for a in strong_root
                 if any(re.search(r"(?<![A-Z0-9])" + re.escape(a[2:]) + r"(?![A-Z0-9.])", f) for f in flat_su)}
        if len(found) != 1:
            continue
        host = strong_root[next(iter(found))]
        if host == root or host not in groups or host not in titled_roots:
            continue
        groups[host].names.update(card.names)
        groups[host].acrs.update(card.acrs)
        del groups[root]
    # 줄여 부른 이름(「혁신지원사업」)이 다른 카드 하나의 긴 이름(「전문대학 혁신지원사업」) 끝과 같으면 같은 사업이다.
    # 긴 이름을 가진 카드가 둘 이상이면(어느 사업인지 모름) 합치지 않는다
    # 앞에 붙은 말이 한 낱말뿐일 때만(「전문대학」+혁신지원사업). 「AID 전환 중점」+전문대학 지원사업처럼 고유한 말이 여럿 붙으면
    # 짧은 쪽은 범주 이름이지 그 사업이 아니다
    flat = lambda n: re.sub(r"[\s()·]", "", n)

    def extends(long_: str, short_: str) -> bool:
        lf, sf = flat(long_), flat(short_)
        if lf == sf or not lf.endswith(sf):
            return False
        return len(long_[: len(long_) - len(short_)].split()) <= 1 if long_.endswith(short_) else len(lf) - len(sf) <= 4
    cards = list(groups.values())
    merged: set[int] = set()
    for short in cards:
        if not short.names or short.acrs:
            continue
        hosts = [c for c in cards if c is not short and id(c) not in merged
                 and all(any(extends(ln, sn) for ln in c.names) for sn in short.names)]
        if len(hosts) == 1:
            hosts[0].names.update(short.names)
            merged.add(id(short))
    cards = [c for c in cards if id(c) not in merged]
    for card in cards:
        if card.names:
            card.key = program_key(card.names.most_common(1)[0][0])
    clean_cards(cards)
    # 막연한 이름만 가진 카드(약칭도 없음)·이름도 약칭도 남지 않은 카드는 사업으로 세우지 않는다 — 문서는 검토 대기로
    return [c for c in cards if (c.acrs or c.names) and (c.acrs or not all(vague_name(n) for n in c.names))]


def clean_cards(cards: list[ProgramCard]) -> dict:
    """카드에 섞인 남의 이름 정리 — 한 카드의 약칭에 다른 사업 약칭·일반어(「대학」「사업」)가 섞이면 다른 사업의 문서와
    질문이 그 카드로 간다. 일반 규칙 셋:
    1) 한글 「약칭」은 그 카드 이름에 「○○사업」으로도 쓰일 때만 남긴다(「대구 앵커사업」의 앵커) — 「(계열)」「(신규)」「(혁신지원)」은 버린다
    2) 같은 영문 약칭이 여러 카드에 있으면 가장 많이 쓰인 카드만 갖는다
    3) 다른 카드가 가진 약칭이 낱말로 든 이름은 그 카드에서 뺀다(「1차년도 RISE사업」이 LINC+ 카드에)
    카드 id 는 정리 전 값으로 고정한다."""
    for c in cards:
        c.fixed_id = c.fixed_id or c.node_id
    stats = {"kor_acr": 0, "shared_acr": 0, "foreign_name": 0}
    for c in cards:
        flat_names = [re.sub(_FLAT, "", n) for n in c.names]
        for a in [a for a in c.acrs if not re.match(r"[A-Za-z]", a)]:
            if not any(re.search(re.escape(re.sub(_FLAT, "", a)) + r"(?:지원)?사업", n) for n in flat_names):
                del c.acrs[a]
                stats["kor_acr"] += 1
    owner: dict[str, tuple[int, ProgramCard]] = {}
    # 약칭이 어떤 카드의 정체(id)와 같으면 쓴 횟수와 상관없이 그 카드 것 — 여러 사업 이름을 흡수한 큰 카드(LINC+)가
    # 남의 약칭을 더 많이 써서 주인이 되지 않게
    by_id = {c.fixed_id: c for c in cards}
    for c in cards:
        for a, n in c.acrs.items():
            k = _acr(a)
            ident = by_id.get("program:" + re.sub(r"[^0-9a-z가-힣]+", "", a.lower()))
            if ident is not None:
                owner[k] = (10 ** 9, ident)
            elif k not in owner or n > owner[k][0]:
                owner[k] = (n, c)
    for c in cards:
        for a in list(c.acrs):
            if owner.get(_acr(a), (0, c))[1] is not c:
                del c.acrs[a]
                stats["shared_acr"] += 1
    # 판 표기(VER2.0·v2)는 약칭이 아니다 — 문서 이름 꼬리의 판 번호가 사업 카드로 서지 않게
    for c in cards:
        for a in [a for a in c.acrs if re.match(r"(?i)^v(?:er)?[\s._]*\d", a)]:
            del c.acrs[a]
    own = {k: oc for k, (_n, oc) in owner.items() if len(k) >= 3}
    for c in cards:
        for nm in list(c.names):
            words = {_acr(w) for w in re.findall(r"[A-Za-z][A-Za-z0-9+.]{2,}", nm)}
            if any(w in own and own[w] is not c for w in words) and not any(own.get(w) is c for w in words):
                del c.names[nm]
                stats["foreign_name"] += 1
    return stats


@dataclass
class Assignment:
    doc_id: int
    program: str = ""                # 노드 id
    program_name: str = ""
    year: int | None = None
    round: int | None = None
    kind: str = ""
    kind_reason: str = ""
    share: float = 0.0
    status: str = "review"           # auto | review
    evidence: list[str] = field(default_factory=list)
    mentions: dict = field(default_factory=dict)       # 문서가 언급한 다른 사업 카드 → 근거(연관 사업 관계용)
    year_src: str = ""               # title | head(본문 앞머리) | path — 기간 밖 판정에서 본문 연도는 약한 근거


def classify(docs: list[dict], cards: list[ProgramCard]) -> list[Assignment]:
    """문서마다 사업·연차·갈래를 정한다. 제목 언급 무게 3, 경로 2, 앞머리 1. 1등이 3점 미만이거나 2등의 두 배에 못 미치면 검토 대기."""
    from zzaimy.app.doc_routing import guess_kind

    # 긴 표기부터. 영문 약칭은 낱말 경계에서만(「DIGITECH」 속 「TECH」는 아니다)
    pool = [(su, c, re.compile((r"(?<![A-Z])" if su[:1].isascii() and su[:1].isalpha() else "") + re.escape(su)
                               + (r"(?![A-Z])" if su[-1:].isascii() and su[-1:].isalpha() else "")))
            for su, c in sorted(((su, c) for c in cards for su in {x.upper() for x in c.surfaces()} if len(su) >= 3),
                                key=lambda t: -len(t[0]))]
    out = []
    for d in docs:
        title = clean_title(d.get("filename") or "")
        head = (d.get("head") or "")[:HEAD_CHARS]
        flat_title = re.sub(_FLAT, "", title).upper()                       # 약칭은 점·밑줄 없이 대조(LINC_3.0 = LINC30)
        flat_path = re.sub(_FLAT, "", d.get("path") or "").upper()
        flat_head = re.sub(_FLAT, "", head).upper()
        scores: Counter = Counter()
        why: dict[str, list[str]] = defaultdict(list)
        # 긴 표기부터 대조하고 대조된 자리는 가린다 — 짧은 이름('전문대학 지원사업')이 긴 이름('AID 전환 중점 전문대학 지원사업')
        # 안에서 또 세지면 모든 언급이 두 사업으로 갈린다
        for su, c, rx in pool:
            if rx.search(flat_title):
                scores[c.node_id] += 3
                why[c.node_id].append(f"제목에 「{su}」")
                flat_title = rx.sub(lambda m: "\0" * len(m.group(0)), flat_title)
            if rx.search(flat_path):                            # 폴더는 파일 제 이름보다 약한 단서 — 다른 사업 문서가 섞여 들어 있다
                scores[c.node_id] += 2
                why[c.node_id].append(f"경로에 「{su}」")
                flat_path = rx.sub(lambda m: "\0" * len(m.group(0)), flat_path)
            n = len(rx.findall(flat_head))
            if n:
                scores[c.node_id] += min(n, 5)
                why[c.node_id].append(f"앞머리에 「{su}」 {n}회")
                flat_head = rx.sub(lambda m: "\0" * len(m.group(0)), flat_head)
        a = Assignment(doc_id=int(d["id"]))
        if scores:
            best, top = scores.most_common(1)[0]
            card = next(c for c in cards if c.node_id == best)
            a.program, a.program_name = best, card.name
            a.share = round(top / sum(scores.values()), 2)
            second = scores.most_common(2)[1][1] if len(scores) > 1 else 0
            # 다른 사업을 함께 언급하는 문서가 많다(LINC3.0 보고서의 RISE·혁신지원 언급) — 몫보다 2등과의 차이로 판정한다
            # 경로만 말하는 파일(「RISE사업(2025~)/…/붙임1.hwp」)은 경로 2점으로 확정한다. 제 이름이 다른 사업을 말하면(3점) 2등의 두 배를 못 넘어 검토로 간다
            floor = 2 if any(w.startswith("경로") for w in why[best]) else 3
            a.status = "auto" if top >= floor and top >= 2 * second else "review"
            a.evidence = why[best][:4]
            # 제목·앞머리에서 함께 언급한 다른 사업(경로만으로 걸린 것은 폴더가 섞인 탓일 수 있어 뺀다)
            a.mentions = {cid: ws[0] for cid, ws in why.items() if cid != best
                          and any(w.startswith(("제목에", "앞머리에")) for w in ws)}
        else:
            a.evidence = ["사업명 언급을 찾지 못함"]
        m = _ROUND.search(title) or _ROUND.search(head[:600])
        a.round = int(m.group(1)) if m else None
        a.year = _first_year(title)
        a.year_src = "title" if a.year else ""
        if a.year is None:
            a.year = body_year(head[:600], _YEAR)
            a.year_src = "head" if a.year else ""
        if _EVAL_RESULT.search(title):                          # 평가 '기준'이 아니라 평가 '결과·의견'
            a.kind, a.kind_reason = "evaluation", "제목에 평가 결과·종합의견"
        else:
            a.kind, a.kind_reason = guess_kind(d.get("filename") or "", head)
        out.append(a)
    return out


# 경로·이름의 연도 — 「2022~2027」 같은 기간 표기(사업 전체 기간)는 연도가 아니다
_PATH_YEAR = re.compile(r"(?<![\d~])((?:19|20)\d{2})(?!\d|\s*~)")


def inherit_by_folder(docs: list[dict], assigned: list[Assignment], min_n: int = 10, share: float = 0.7,
                      min_year_docs: int = 3) -> int:
    """사업명이 없는 폴더의 파일에 상위 폴더의 사업을 물려준다 — 「링크/2차년도(2023)/…」처럼 폴더가 사업을 말하지 않을 때.

    가장 가까운 상위 폴더(확정 파일 min_n 건 이상)에서 한 사업이 share 이상이면 그 사업으로 두되 상태는 'folder'(검토 대상,
    자동 확정 아님). 파일 경로에 연도가 있으면 그 연도를 다루는 사업만 후보로 센다 — 사업마다 확정 파일의 연도 범위로
    운영 기간을 어림한다(LINC+ 2017~2021 폴더 아래의 2023 파일은 LINC+ 가 아니다). 물려준 건수를 돌려준다.
    """
    def ancestors(path: str) -> list[str]:
        parts = [p for p in re.split(r"[\\/]", path or "") if p]
        return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]

    def year_of(d: dict) -> int | None:
        ys = [y for y in _PATH_YEAR.findall(f"{d.get('path') or ''}/{d.get('filename') or ''}") if plausible_year(y)]
        return int(ys[-1]) if ys else None

    under: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, Counter] = defaultdict(Counter)
    names = {}
    for d, a in zip(docs, assigned):
        if a.program and a.status == "auto":
            names[a.program] = a.program_name
            for anc in ancestors(d.get("path") or ""):
                under[anc][a.program] += 1
            y = a.year or year_of(d)
            if y:
                years[a.program][y] += 1
    span = {p: (min(ys), max(ys)) for p, c in years.items()
            if (ys := [y for y, n in c.items() if n >= min_year_docs])}
    n_inherited = 0
    for d, a in zip(docs, assigned):
        if a.program and a.status == "auto":
            continue
        y = year_of(d)
        for anc in ancestors(d.get("path") or ""):
            c = Counter({p: n for p, n in under.get(anc, {}).items()
                         if y is None or p not in span or span[p][0] <= y <= span[p][1]})
            total = sum(c.values())
            if total < min_n:
                continue
            best, n = c.most_common(1)[0]
            if n / total >= share and (not a.program or a.program == best):
                a.program, a.program_name, a.status = best, names.get(best, best), "folder"
                a.share = round(n / total, 2)
                a.evidence = [f"상위 폴더 「{anc[-40:]}」 확정 {total}건 중 {n}건이 이 사업"
                              + (f"(연도 {y} 를 다루는 사업만)" if y else "")]
                n_inherited += 1
            break
    return n_inherited


_SEG_YEAR = re.compile(r"(?<![\d~])((?:19|20)\d{2})\s*(?:년|학년도|\))|\((?:19|20)\d{2}\)|^((?:19|20)\d{2})$")
_PERIOD = re.compile(r"((?:19|20)\d{2})\s*(?:\.\d{1,2})?\s*~\s*((?:19|20)\d{2})?")


def ledger_link(cards: list, ledger: dict) -> dict:
    """외부 확인 장부(출처 있고 기간이 있는 사업)와 문서 카드를 잇는다.

    돌려주는 것: {"periods": {카드 id: (시작, 끝)}, "spans": [{id, name, start, end, terms}], "aliases": {카드 id: 대표 카드 id}}
    - 카드 하나에 장부 항목이 여럿 이어지면(문서 카드가 앞뒤 단계 이름을 함께 들고 있을 때 — LINC+ 카드의 「산학협력 선도전문대학」)
      가장 긴 표기가 맞은 항목을 그 카드의 사업(주인)으로 보고 그 기간만 카드에 쓴다
    - 주인으로 이어진 장부 항목은 카드 id 를 그대로 쓴다(같은 사업이 장부 id·카드 id 로 갈리지 않게). 이어진 카드가 없는 항목
      (앞 단계 LINC 1단계처럼 문서 카드가 따로 없는 사업)은 장부 이름의 program_key
    - 장부 항목 하나가 여러 카드의 주인이면 같은 사업이다 — 가장 긴 표기가 맞은 카드로 합친다(「혁신지원사업」 = 「전문대학 혁신지원사업」)
    acronyms 는 문서 대조에만 쓰고 카드 대조에는 쓰지 않는다."""
    # 판 번호의 「.0」은 떼고 견준다 — 파일 이름은 「LINC3_성과달성계획」, 장부는 「LINC 3.0」처럼 쓰여 그대로 견주면
    # 같은 사업이 두 카드로 갈린다. 「3.0」 과 「30」 은 다르므로 점을 지우기 전에 뗀다.
    flat = lambda t: re.sub(r"[\s.()·\-_]+", "", re.sub(r"(\d)\.0(?!\d)", r"\1", t or "")).upper()
    entries = []
    for e in ledger.get("programs", []):
        m = _PERIOD.search(str(e.get("period") or ""))
        terms = [t for t in e.get("terms", []) if t]
        if e.get("sources") and terms:               # 기간이 없어도 같은 사업 합치기에는 쓴다(기간·환산은 기간이 있을 때만)
            entries.append((e, int(m.group(1)) if m else None, (int(m.group(2)) if m.group(2) else None) if m else None, terms))
    # 카드마다 맞은 항목과 가장 긴 맞은 표기 길이
    owner: dict[str, tuple[int, int]] = {}                 # 카드 id → (항목 번호, 맞은 길이)
    # 끝의 「사업」「지원사업」은 떼고도 견준다 — 장부 「대학일자리플러스센터」 = 카드 「대학일자리플러스센터 사업」
    # 「지원사업」의 지원은 이름의 일부일 수 있다(혁신지원사업) — 끝의 「사업」만 뗀다
    core = lambda t: (lambda f: f[:-2] if f.endswith("사업") and len(f) >= 6 else f)(flat(t))
    # 카드 id 꼬리가 영문 약칭(program:abcd30)이면 그 약칭을 표기로 가진 항목이 먼저다 — 앞 단계 사업의 긴 이름이 본문에서 섞여
    # 들어와 더 길게 맞아도(「…(ABCD+) 육성사업」) 카드의 정체가 가리키는 항목의 주인이 된다
    id_form = lambda t: re.sub(r"[^0-9a-z가-힣]+", "", (t or "").lower())
    rank: dict[str, tuple[bool, int]] = {}
    for i, (_e, _s, _t, terms) in enumerate(entries):
        want = {flat(t) for t in terms} | {core(t) for t in terms}
        for c in cards:
            hit = want & ({flat(x) for x in c.surfaces()} | {core(x) for x in c.surfaces()})
            if hit:
                tail = c.node_id.split(":", 1)[-1]
                r = (bool(_id_acr(c.node_id)) and any(id_form(t) == tail for t in terms), max(len(h) for h in hit))
                if c.node_id not in owner or r > rank[c.node_id]:
                    owner[c.node_id], rank[c.node_id] = (i, r[1]), r
    # 대표 카드 순서 — 표기 그대로 맞은 카드, 긴 표기, 문서에서 많이 쓰인 카드 순(합칠 때 문서 많은 원래 카드의 id 를 지킨다.
    # 「…사업」을 떼고 맞은 작은 카드가 대표가 되면 기존 문서들의 사업 id 가 바뀐다)
    size = {c.node_id: sum(getattr(c, "names", {}).values()) + sum(getattr(c, "acrs", {}).values()) for c in cards}
    exact = {}
    for i, (_e, _s, _t, terms) in enumerate(entries):
        want = {flat(t) for t in terms}
        for c in cards:
            if want & {flat(x) for x in c.surfaces()}:
                exact[(i, c.node_id)] = True
    by_entry: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for cid, (i, ln) in owner.items():
        by_entry[i].append((cid, ln))
    for i in by_entry:
        by_entry[i].sort(key=lambda t: (not exact.get((i, t[0])), not rank[t[0]][0], -t[1], -size.get(t[0], 0)))
    periods: dict[str, tuple[int, int | None]] = {}
    aliases: dict[str, str] = {}
    spans = []
    for i, (e, start, end, terms) in enumerate(entries):
        owners = by_entry.get(i, [])
        sid = owners[0][0] if owners else "program:" + program_key(terms[-1])
        for cid, _ln in owners:
            if start is not None:
                periods[cid] = (start, end)
            if cid != sid:
                aliases[cid] = sid
        if start is None:
            continue
        match = terms + [t for t in e.get("acronyms", []) if t]
        spans.append({"id": sid, "name": e.get("name") or terms[-1], "start": start, "end": end,
                      "terms": sorted({re.sub(r"[\s.·\-_]+", "", t).upper() for t in match}, key=len, reverse=True)})
    owner_of = {}
    matched = set()
    for i, (_e, _s, _t, terms) in enumerate(entries):
        owners = by_entry.get(i, [])
        if owners:
            owner_of[terms[0]] = owners[0][0]
        want = {flat(t) for t in terms} | {core(t) for t in terms}
        if any(want & ({flat(x) for x in c.surfaces()} | {core(x) for x in c.surfaces()}) for c in cards):
            matched.add(terms[0])
    # 정식 이름은 그 항목의 대표 카드(주인 가운데 가장 긴 표기)에 — 같은 사업으로 합쳐지는 다른 카드가 이름을 가져가지 않게
    display = {}
    for i, (e, _s, _t, terms) in enumerate(entries):
        owners = by_entry.get(i, [])
        if e.get("name") and owners:
            display[owners[0][0]] = e["name"]
    return {"periods": periods, "spans": spans, "aliases": aliases, "owner_of": owner_of, "matched": matched, "display": display}


def apply_not_programs(assigned: list, cards: list, ledger: dict, link: dict) -> int:
    """장부가 출처와 함께 「사업 아님」(category)으로 적은 항목 — 그 카드에 배정된 문서를 사업 없음(기관 업무, 검토 판정과 같은
    'agent' 상태)으로 돌린다. 카드는 남겨 두어 그 이름의 문서가 다른 사업으로 잘못 가지 않게 한다. 바꾼 문서 수를 돌려준다."""
    why: dict[str, str] = {}
    for e in ledger.get("programs", []):
        terms = [t for t in e.get("terms", []) if t]
        if e.get("category") == "사업 아님" and e.get("sources") and terms:
            cid = link.get("owner_of", {}).get(terms[0])
            if cid:
                why[cid] = (e.get("name") or terms[0]) + (f" — {e['note'][:60]}" if e.get("note") else "")
    for c in cards:
        if c.node_id in why:
            c.not_program = True
    n = 0
    for a in assigned:
        if a.program in why:
            a.evidence = [f"외부 확인: 사업 아님 — {why[a.program]}"]
            a.program, a.program_name, a.status = "", "", "agent"
            n += 1
    return n


def related_programs(assigned: list, min_docs: int = 5, min_share: float = 0.02) -> list[dict]:
    """문서 근거로 잇는 연관 사업 — 사업 A 에 배정된 문서가 제목·앞머리에서 사업 B 를 함께 다루면 한 건.
    min_docs 건 이상이고 A 문서의 min_share 이상일 때만 관계로(한두 문서의 우연한 언급은 연관이 아니다, 절대 규칙 12).
    돌려주는 것: [{src, dst, n, share, docs:[문서 번호 …]}]"""
    per_prog = Counter(a.program for a in assigned if a.program)
    pair_docs: dict[tuple[str, str], list[int]] = defaultdict(list)
    for a in assigned:
        if not a.program:
            continue
        for other in a.mentions:
            if other != a.program:
                pair_docs[(a.program, other)].append(a.doc_id)
    out = []
    for (src, dst), ds in pair_docs.items():
        share = len(ds) / max(per_prog[src], 1)
        if len(ds) >= min_docs and share >= min_share and dst in per_prog:
            out.append({"src": src, "dst": dst, "n": len(ds), "share": round(share, 3), "docs": ds[:5]})
    return sorted(out, key=lambda r: -r["n"])


def merge_aliases(cards: list, link: dict) -> list:
    """장부가 같은 사업이라고 한 카드(aliases: 카드 → 대표 카드)를 대표 카드 하나로 실제로 합친다 — 이름·약칭을 더하고 남는 카드는
    뺀다. 문서만 옮기고 카드를 남겨 두면 같은 정식 이름의 빈 카드가 생겨 질문이 그리로 간다."""
    al = link.get("aliases") or {}
    by_id = {c.node_id: c for c in cards}
    for cid, rep in al.items():
        src, dst = by_id.get(cid), by_id.get(rep)
        if src is None or dst is None or src is dst:
            continue
        dst.names.update(src.names)
        for a, n in src.acrs.items():
            dst.acrs[a] = max(dst.acrs[a], n)
        dst.renamed = list(dst.renamed) + [r for r in src.renamed if r not in dst.renamed]
    return [c for c in cards if c.node_id not in al or by_id.get(al[c.node_id]) is None]


def apply_display(cards: list, link: dict) -> int:
    """장부에 정식 이름(name)이 있는 사업은 그 이름을 카드 표시 이름으로 — 문서에 가장 많이 나온 이름(「앵커」·「RISE사업」)이
    사업 이름이 되지 않게. 그래프(157)·원본 장부(170)가 같은 규칙을 쓴다."""
    n = 0
    for c in cards:
        nm = (link.get("display") or {}).get(c.node_id)
        if nm:
            c.display = nm
            n += 1
    return n


def program_periods(cards: list, ledger: dict) -> dict[str, tuple[int, int | None]]:
    """카드 id → 사업 기간(주인 항목 기준). ledger_link 참조."""
    return ledger_link(cards, ledger)["periods"]


def ledger_spans(ledger: dict, cards: list | None = None) -> list[dict]:
    """장부 사업마다 {id, name, start, end, terms} — 카드가 있으면 주인 카드 id 를 쓴다. ledger_link 참조."""
    return ledger_link(cards or [], ledger)["spans"]


def _span_match(text: str, year: int, spans: list[dict]) -> dict | None:
    """연도를 기간에 품고 이름·약칭이 글에 있는 사업 — 가장 긴 표기가 맞은 것. 영문 약칭은 낱말 경계에서만."""
    best, best_len = None, 0
    for sp in spans:
        if not (sp["start"] <= year <= (sp["end"] or 9999)):
            continue
        for t in sp["terms"]:
            rx = (r"(?<![A-Z])" if t[:1].isascii() and t[:1].isalpha() else "") + re.escape(t) + \
                 (r"(?![A-Z+])" if t[-1:].isascii() and t[-1:].isalpha() else "")
            if len(t) > best_len and re.search(rx, text):
                best, best_len = sp, len(t)
    return best


def fill_period(docs: list[dict], assigned: list, periods: dict[str, tuple[int, int | None]],
                spans: list[dict] | None = None, aliases: dict[str, str] | None = None,
                card_names: dict[str, str] | None = None) -> dict[str, int]:
    """연차·연도를 폴더 경로로 채우고 사업 기간으로 서로 환산한다. 기간 밖 연도면 그 사업으로 확정하지 않는다(검토).

    - 연차: 파일 이름에 없으면 가장 깊은 폴더의 「N차년도」
    - 연도: 파일 이름에 없으면 가장 깊은 폴더의 「2023년」「(2023)」「2023학년도」 또는 「2023」 폴더 — 「2022~2027」 같은 기간은 연도가 아니다
    - 사업 시작 연도를 알면 연차 ↔ 연도 환산(연차 N = 시작 + N - 1)
    - 연도가 사업 기간 밖이면 사업을 비우고 status 'review'(근거 남김) — 「2014년 LINC+」처럼 앞 단계 사업 자료가 섞이지 않게
    파일 이름의 연도는 작성일일 수 있고 폴더의 연도가 수행 연도 묶음인 경우가 많다 — 그래서 폴더 근거를 먼저 본다."""
    stats = {"round_from_path": 0, "year_from_path": 0, "converted": 0, "out_of_period": 0, "merged_alias": 0}
    names = {sp["id"]: sp["name"] for sp in (spans or [])}
    for d, a in zip(docs, assigned):
        if not a.program:
            continue
        if aliases and a.program in aliases:              # 장부가 같은 사업이라고 한 카드 — 대표 카드로
            a.evidence = list(a.evidence) + [f"외부 확인 장부: 「{a.program_name}」 = 대표 사업 {aliases[a.program]}"]
            a.program = aliases[a.program]
            a.program_name = (card_names or {}).get(a.program) or names.get(a.program) or a.program_name
            stats["merged_alias"] += 1
        segs = [x for x in re.split(r"[\\/]", d.get("path") or "") if x]
        if a.round is None:
            for seg in reversed(segs):
                m = _ROUND.search(seg)
                if m:
                    a.round = int(m.group(1))
                    stats["round_from_path"] += 1
                    break
        if a.year is None:
            # 본문 첫머리 연도(연혁 표기를 거른 것)는 폴더 연도로 덮지 않는다 — 폴더를 앞세우면 「2019년도 실적보고서」가
            # 5차년도(2021) 폴더 연도로 바뀐다
            for seg in reversed(segs):
                m = _SEG_YEAR.search(seg.strip())
                if m and plausible_year(re.search(r"\d{4}", m.group(0)).group(0)):
                    a.year = int(re.search(r"\d{4}", m.group(0)).group(0))
                    a.year_src = "path"
                    stats["year_from_path"] += 1
                    break
        span = periods.get(a.program)
        if not span:
            continue
        start, end = span
        if a.year is None and a.round:
            a.year = start + a.round - 1
            a.evidence = list(a.evidence) + [f"{a.round}차년도 · 사업 시작 {start}년 → {a.year}년"]
            stats["converted"] += 1
        elif a.year and start <= a.year <= (end or 9999) and a.round != a.year - start + 1:
            # 연차가 없거나 연도와 엇갈리면 연도로 맞춘다 — 「N차년도」는 준비년도를 0차로 세는 등 셈이 제각각이고
            # 연도가 더 믿을 만하다(「LINC+ 6차년도 (2021)」「LINC3.0 6차년도 (2022)」 같은 엇갈린 연차 노드)
            if a.round is not None:
                stats["round_fixed"] = stats.get("round_fixed", 0) + 1
            a.round = a.year - start + 1
            a.evidence = list(a.evidence) + [f"{a.year}년 · 사업 시작 {start}년 → {a.round}차년도"]
            stats["converted"] += 1
        if a.year and not (start <= a.year <= (end or 9999)):
            text = re.sub(r"[\s.·\-_]+", "", f"{d.get('path') or ''}/{d.get('filename') or ''}").upper()
            other = _span_match(text, a.year, [sp for sp in (spans or []) if sp["id"] != a.program])
            if other:                                     # 기간이 맞고 이름·약칭이 경로에 있는 앞뒤 단계 사업
                a.evidence = list(a.evidence) + [f"연도 {a.year} 가 「{a.program_name}」 기간({start}~{end or ''}) 밖 — "
                                                 f"기간({other['start']}~{other['end'] or ''})과 이름이 맞는 「{other['name']}」로"]
                a.program, a.program_name, a.status = other["id"], (card_names or {}).get(other["id"]) or other["name"], "period"
                a.round = a.year - other["start"] + 1
                stats["moved_to_period_program"] = stats.get("moved_to_period_program", 0) + 1
                continue
            if getattr(a, "year_src", "") == "head":
                # 앞뒤 단계 사업도 맞지 않고 연도가 본문 앞머리에서 우연히 잡힌 것(협약 기업 설립 연도, 첫해 보고서의 직전 연도 실적)이면
                # 수행 연도가 아니다 — 사업은 두고 연도만 버린다(버리지 않으면 사업 폴더 문서가 대거 미분류로 남는다).
                # 앞뒤 단계로 옮기는 위 규칙이 먼저다 — 순서를 바꾸면 뒤 단계 사업 문서가 앞 단계 사업에 남는다
                a.evidence = list(a.evidence) + [f"본문 연도 {a.year} 가 이 사업 기간({start}~{end or ''}) 밖 — 연도만 버림"]
                a.year = None
                if a.round and 1 <= a.round <= ((end or start + 11) - start + 1):
                    a.year = start + a.round - 1
                else:
                    a.round = None
                stats["head_year_dropped"] = stats.get("head_year_dropped", 0) + 1
                continue
            own = next((sp for sp in (spans or []) if sp["id"] == a.program), None)
            fname = re.sub(r"[\s.·\-_]+", "", d.get("filename") or "").upper()
            if own and start - 2 <= a.year <= (end or 9999) + 1 and any(t and t in fname for t in own["terms"]):
                # 파일 이름이 이 사업을 직접 부르고 연도는 기간 바로 앞뒤 — 「(신)2021학년도 교육과정표(LINC3.0)」처럼 신청 때 낸
                # 직전 학년도 기준 자료나 종료 이듬해 정리 자료다. 사업은 두고 연도만 버린다.
                # 기간에서 더 먼 연도(「2014년 LINC+」)는 앞 단계 자료일 수 있어 아래처럼 확정하지 않는다
                a.evidence = list(a.evidence) + [f"파일 이름이 이 사업을 부르고 연도 {a.year} 는 기간({start}~{end or ''}) 바로 앞뒤 — 연도만 버림"]
                a.year = a.round = None
                stats["named_near_year_dropped"] = stats.get("named_near_year_dropped", 0) + 1
                continue
            a.evidence = list(a.evidence) + [f"연도 {a.year} 가 이 사업 기간({start}~{end or ''}) 밖 — 확정하지 않음"]
            a.program, a.program_name, a.status = "", "", "review"
            a.year = a.round = None
            stats["out_of_period"] += 1
    return stats


def load_reviews(path) -> list[dict]:
    """에이전트·사람 검토 장부(class_review.jsonl) — 폴더 단위 판정. 확신도 낮음은 쓰지 않는다."""
    import json as _json
    from pathlib import Path as _P
    out = []
    p = _P(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = _json.loads(line)
        except ValueError:
            continue
        if r.get("folder") and r.get("label") and r.get("confidence") in ("high", "medium"):
            out.append(r)
    # 교차 검수(다른 검토자의 장부 <이름>_astra.jsonl) — 판정이 엇갈린 폴더는 쓰지 않는다(사람 검토 대기)
    cross = p.with_name(p.stem + "_astra.jsonl")
    if cross.is_file():
        disagree = set()
        for line in cross.read_text(encoding="utf-8").splitlines():
            try:
                c = _json.loads(line)
            except ValueError:
                continue
            if c.get("folder") and c.get("agree") is False:
                disagree.add(c["folder"])
        out = [r for r in out if r["folder"] not in disagree]
    return out


def apply_reviews(docs: list[dict], assigned: list[Assignment], reviews: list[dict], cards: list[ProgramCard]) -> int:
    """규칙 분류가 확정하지 못한 파일(검토 대기·폴더 추론·사업 없음)에 폴더 검토 판정을 쓴다 — 가장 긴 폴더가 이긴다.

    판정이 사업 이름이면 그 카드(없으면 새 카드), 「사업 아님」이면 사업 없음(기관 업무), 「새 사업: X」면 X 카드를 만든다.
    규칙이 확정(auto)한 파일은 건드리지 않는다. 상태는 'agent'(에이전트 검토)·근거는 판정 이유. 바꾼 수를 돌려준다."""
    if not reviews:
        return 0
    by_folder = sorted(reviews, key=lambda r: -len(r["folder"]))

    def card_named(name: str) -> ProgramCard:
        key = program_key(name)
        loose = lambda t: re.sub(r"[\s().·\-_0-9]", "", t or "").upper()
        for c in cards:
            if c.name == name or c.key == key or name in c.names:
                return c
        for c in cards:                                   # 표기만 조금 다른 같은 이름(띄어쓰기·괄호·단계 숫자)
            if any(loose(n) == loose(name) for n in list(c.names) + [c.name]):
                return c
        c = ProgramCard(key=key or name)
        c.names[name] += 1
        cards.append(c)
        return c
    n = 0
    for d, a in zip(docs, assigned):
        if a.status == "auto":
            continue
        rel_dir = (d.get("path") or "").strip("/")
        hit = next((r for r in by_folder if rel_dir == r["folder"] or rel_dir.startswith(r["folder"] + "/")), None)
        if hit is None:
            continue
        label = hit["label"].strip()
        # 파일 제 이름에 사업이 적힌 문서는 폴더 판정이 다른 사업으로 덮지 않는다 — 폴더에는 다른 사업 문서가 섞인다
        # (예: 한 사업 폴더 안에 든 다른 사업의 작성 서식)
        if a.program and any(e.startswith("제목에") for e in a.evidence):
            target = "" if label == "사업 아님" else (label.split(":", 1)[1].strip() if label.startswith("새 사업") else label)
            if not target or program_key(target) != program_key(a.program_name) and target not in (
                    next((c for c in cards if c.node_id == a.program), ProgramCard(key="")).names):
                continue
        if label == "사업 아님":
            a.program, a.program_name = "", ""
            a.status = "agent"
            a.evidence = [f"검토({hit.get('reviewer', '에이전트')}): 사업 아님 — {hit.get('reason', '')[:80]}"]
        else:
            name = label.split(":", 1)[1].strip() if label.startswith("새 사업") else label
            c = card_named(name)
            a.program, a.program_name, a.status = c.node_id, c.name, "agent"
            a.evidence = [f"검토({hit.get('reviewer', '에이전트')}): {hit.get('reason', '')[:80]}"]
        n += 1
    return n
