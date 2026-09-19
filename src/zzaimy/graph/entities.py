"""개체 추출 — 역할이 있는 것만 뽑고, 문서의 '정체'를 먼저 정한다 (온톨로지 v2).

왜 다시 썼나
  이전 구현은 문서에서 접미사로 끝나는 낱말을 모두 개체로 올리고, 같은 낱말이
  나오면 문서를 이었다. 실측(corpus_pilot 133문서)에서 그 결과는 이랬다:
  한국연구재단 62문서 · 평가위원회 33문서 · 사업관리위원회 25문서 · 운영위원회
  18문서 · OO대학교 15문서 · 대학본부 12문서. 어느 공고에나 나오는 역할 명칭이라
  문서를 이어 봐야 아무 뜻이 없고(간선 774개가 전부 이런 언급 간선이었다),
  정작 문서의 정체인 사업명(HUSS·글로컬대학·BRIDGE3.0·첨단분야 혁신융합대학)은
  하나도 잡히지 않았다 — 패턴의 접미사 목록에 없었기 때문이다.

바뀐 원칙
  1. 개체에는 유형이 있다. 맨 명사는 개체가 아니다.
     program(사업) · org(기관) · law(근거 법령·조항) · year · period(신청기간)
     · money(지원 규모) · target(지원 대상)
  2. 흔한 말은 신호가 아니다. 불용어를 손으로 나열하지 않고 코퍼스에서 계산한다.
     · 허브 배제: 문서 √N건 초과에 나오는 개체는 연결 근거로 쓰지 않는다.
       개체 하나가 만드는 문서 쌍은 d(d-1)/2 이므로, d를 √N으로 묶으면 한 개체가
       만드는 간선이 N/2를 넘지 않는다 — 코퍼스 크기에 따라 자동으로 조정된다.
     · 역할어 배제: 복합어의 수식부가 그 복합어 밖에서도 흔하면(응집도 낮음)
       고유명이 아니라 역할 명칭이다. 평가/운영/사업관리 + 위원회가 여기 걸린다.
  3. 문서의 정체(어떤 사업에 관한 문서인가)를 먼저 정한다. 제목에서 되풀이되는
     최장 어구를 사업명 후보로 캐고, 한국어 제목이 '수식어 + 역할어' 순서라는
     성질을 써서(평균 등장 위치) 사업명과 문서 역할어를 가른다. 실측 분리도:
     사업명 후보 평균 위치 0.00~0.14, 역할어 0.50~0.80.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# ── 유형별 추출 패턴 ────────────────────────────────────────────────────────
# 연도 — "2026년" 형태만 (숫자 단독은 수치와 혼동)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*년")

# 기관 — 판별력 있는 복합 접미사. 이름부는 한글·영문·숫자 2~18자.
_ORG_SUFFIX = (
    "사업단|위원회|지원센터|훈련센터|평가원|진흥원|연구원|장학재단|재단|"
    "공단|공사|본부|대학교|협의회|추진단|사무국|교육청|연구소"
)
# 이름부는 10자 이내. 띄어쓰기가 사라진 OCR 문장에서는 앞 문장이 통째로 이름부로
# 빨려 들어간다(실측: '실행계획미이행또는성과미흡시위원회'). 길이 상한과, 이름부
# 안에 절 경계를 만드는 조사가 있는지로 걸러낸다.
_ORG = re.compile(rf"([가-힣A-Za-z0-9]{{2,10}}?)({_ORG_SUFFIX})")
# 이름부 3번째 글자부터 나타나는 조사 + 이어지는 한글 = 문장을 삼킨 흔적
# (첫 두 글자는 '이화여자'처럼 이름의 일부일 수 있어 제외한다)
_CLAUSE_IN_NAME = re.compile(r"(?<=.{2})[은는이가을를에의로와과](?=[가-힣])")

# 명명된 사업·제도 — 코퍼스가 작아 제목에서 사업명을 캘 수 없을 때의 보조 경로.
# 주 경로는 mine_programs(제목에서 되풀이되는 어구)이고, 이 패턴은 문서 한두 건만
# 있는 환경에서도 사업이 보이게 하는 안전망이다. 연결에 쓸지는 문서 빈도가 정한다.
_PROGRAM = re.compile(
    r"[가-힣A-Za-z0-9()]{2,24}(?:지원사업|육성사업|혁신사업|훈련과정|장학금|전형)")
# 범주 그 자체인 말 — 이름이 아니다. 문서가 몇 건뿐이라 빈도 통계가 성립하지 않는
# 환경(신규 설치 직후)에서만 쓰는 최소 안전망이며, 실제 방어는 linkable_entities의
# 문서 빈도·응집도 계산이 한다.
_GENERIC = {
    "지원사업", "육성사업", "혁신사업", "국고지원사업", "재정지원사업",
    "장학금", "전형", "훈련과정", "대학교", "본부", "재단", "위원회",
}

# 근거 법령·조항 — 「…법」 제N조 / …에 관한 법률 제N조. 조문까지 잡으면 근거가
# 같은 문서끼리 이을 수 있다(법률명만으로는 너무 넓다).
# 「」는 강조 표시로도 쓰인다(실측: 「글로컬대학」·「첨단분야 혁신융합대학」이 법령으로
# 잡혔다). 법령으로 인정하려면 이름이 법령 접미사로 끝나야 한다.
_LAW_TAIL = r"(?:법률|법|시행령|시행규칙|령|규칙|규정|지침|조례|정관)"
_LAW = re.compile(
    rf"[「『]\s*([^」』\n]{{2,40}}?{_LAW_TAIL})\s*[」』]"
    rf"\s*(?:제\s*(\d+)\s*조(?:\s*의\s*(\d+))?)?"
    rf"|([가-힣]{{2,20}}{_LAW_TAIL})\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?"
)

# 신청·접수 기간 — 날짜 범위
_PERIOD = re.compile(
    r"((?:19|20)\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?)"
    r"\s*[~∼–—-]\s*"
    r"((?:(?:19|20)\d{2}\s*[.\-/년]\s*)?\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?)"
)
# 지원 규모 — 금액(억/백만/천만 원)
_MONEY = re.compile(r"(\d[\d,.]*\s*(?:조|억|천만|백만|만)?\s*원)")
# 지원 대상·자격 — "…을(를) 대상으로", "신청 자격 : …", "지원대상 : …"
_TARGET = re.compile(
    r"(?:지원\s*대상|신청\s*자격|지원\s*자격|참여\s*자격|신청\s*대상)\s*[:：]\s*([^\n]{4,60})")

# 제목 앞머리의 편철 표기 — (붙임1) [별첨2] 붙임 3. 서식1 ★ 1. 등
_TITLE_LEAD = re.compile(
    r"^\s*(?:[★☆]+"
    r"|[\[(［（]\s*(?:붙임|별첨|서식|참고|공고문|별지|부록)?\s*\d*\s*[-.]?\d*\s*[)\]）］]"
    r"|(?:붙임|별첨|참고|서식|별지|부록)\s*\d*\s*[.]?"
    r"|\d{1,2}\s*[.]"
    r"|[-–—]\s*)+")
_TITLE_YEAR = re.compile(r"^\s*(?:\d{6}_)?(?:19|20)\d{2}\s*(?:년도?|[-~](?:19|20)\d{2}년?)?\s*")
_TITLE_EXT = re.compile(r"\.(pdf|hwpx?|docx?|pptx?|txt|png|jpe?g)$", re.I)
_TITLE_TOKEN = re.compile(
    r"[가-힣A-Za-z0-9]+(?:\.\d+)?(?:\([A-Za-z가-힣0-9]{1,12}\))?|\([A-Za-z가-힣0-9]{1,12}\)")

# ── 경계값(근거는 주석) ─────────────────────────────────────────────────────
# 제목 n-gram 최대 길이 — 한국어 사업명은 대체로 6어절 이내
MAX_TITLE_NGRAM = 6
# 사업명 후보가 되려면 서로 다른 문서 이 건수 이상에서 되풀이돼야 한다
MIN_CORE_DOCS = 2
# 제목 안 평균 등장 위치 — 이보다 앞이면 수식부(=사업명), 뒤면 역할어.
# 실측 corpus_pilot: 사업명 0.00~0.14 / 역할어(양식·공고·기본계획·신청서) 0.50~0.80
CORE_MAX_POSITION = 0.25
# 낱말 하나의 역할어 판정 — 제목 안 평균 위치가 이보다 뒤면 문서 종류를 가리키는 말.
# 실측: 사업 0.37 · 사업계획서 0.54 · 신청서 0.68 · 서식 0.69 · 기본계획 0.70
# · 확약서 0.72 · 양식 0.78 · 공고 0.79. 0.45가 '사업'과 나머지를 가른다.
ROLE_WORD_POSITION = 0.45
# 긴 후보가 짧은 후보를 흡수하는 기준 — 문서 수 비율. 표기가 하나 빠진 변형
# ("…(HUSS)" 없는 제목)은 문서 수가 비슷하므로 흡수하고, 절반 아래로 떨어지면
# 별개의 하위 조직·사업으로 본다(대학혁신지원사업 12건 ↔ 총괄협의회 6건).
ABSORB_RATIO = 0.6
# 제목 어구가 사업으로 승격되려면 본문이 뒷받침해야 한다. 파일 이름에만 있는 어구는
# 작명 습관이지 사업이 아니다. 실측(운영 DB 교내문서 39건): 제목에서 캔 후보 18개 중
# 14개가 본문 등장 0건이었다("학년도 입학자 연계교육과정 편성표 학과 계열" 등).
# 반대로 국고 묶음의 진짜 사업은 모두 본문 2문서 이상·평균 2.5회 이상 나온다.
PROGRAM_MIN_BODY_DOCS = MIN_CORE_DOCS      # 본문에 나오는 문서 수
PROGRAM_MIN_AVG_MENTIONS = 2.0             # 그 문서들에서의 평균 등장 횟수
# '하나의 이름으로 불리는가' — 본문에서 조사가 붙거나 인용부호로 묶여 명사구로 쓰인
# 횟수. 실측: 진짜 사업은 2문서 이상에서 이렇게 쓰이고(BRIDGE3.0 2 · SCOUT 4 ·
# 글로컬대학 31), 문서 종류·절차명은 0이다(가구원 정보제공 동의 절차 0 ·
# 산학연협력 EXPO 개요 및 개최 안내 0).
PROGRAM_MIN_NAMED_DOCS = MIN_CORE_DOCS
# 본문에서 사업 정체를 찾을 때 — 이 횟수 이상 나와야 인정한다
MIN_BODY_MENTIONS = 2
# 본문에서 정체를 볼 범위(앞부분). 표지·개요에 사업명이 나온다
BODY_HEAD_CHARS = 4000
# 연결 근거가 되려면 서로 다른 문서 이 건수 이상에 나와야 한다 — 한 문서에만 있는
# 이름은 그 문서를 아무와도 잇지 못하므로 그래프에 올릴 이유가 없다.
MIN_LINK_DOCS = 2
# 복합어 응집도 — df(복합어)/df(수식부). 낮으면 수식부가 밖에서도 흔한 일반어라
# 그 복합어는 고유명이 아니라 역할 명칭이다(평가+위원회, 운영+위원회, 대학+본부).
MIN_COHESION = 0.5
# 자리표시자(마스킹·서식 견본) — 같은 글자의 반복이나 O·○·X로만 된 이름
_PLACEHOLDER = re.compile(r"^(?:[OoＯ○◯ㅇXx×]{2,}|(.)\1{1,})[가-힣A-Za-z]*$")


def normalize_entity(name: str) -> str:
    """개체명 표기 정규화 — 내부 공백·감싸는 문장부호 제거(표기 흔들림 흡수)."""
    name = re.sub(r"\s+", "", (name or "").strip())
    return name.strip("·.,;:()[]{}<>「」『』\"'")


def clean_title(filename: str) -> str:
    """파일명에서 편철 표기·확장자·연도 접두를 걷어낸 제목."""
    t = _TITLE_EXT.sub("", filename or "").replace("+", " ")
    prev = None
    while prev != t:
        prev = t
        t = _TITLE_LEAD.sub("", t).strip()
        t = _TITLE_YEAR.sub("", t).strip()
    t = re.sub(r"_\d+$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def program_key(name: str) -> str:
    """사업명 대조 키 — 공백·괄호 약칭·말미 '사업'을 떼어 표기 흔들림을 흡수한다.

    "인문사회 융합인재양성사업(HUSS)"·"인문사회 융합인재양성사업" → 같은 키,
    "첨단분야 혁신융합대학"·"첨단분야 혁신융합대학사업" → 같은 키.
    """
    s = re.sub(r"\([^)]*\)", "", name or "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"(?:사업단|사업)$", "", s)
    return s


def acronyms(name: str) -> list[str]:
    """이름에 붙은 괄호 약칭 — "…(HUSS)" → ["HUSS"]."""
    return [a for a in re.findall(r"\(([A-Za-z0-9가-힣]{2,12})\)", name or "")]


# ── 1단계: 코퍼스에서 사업명(정체) 캐기 ─────────────────────────────────────


@dataclass
class Program:
    """사업 정체 하나 — 대표 표기와 별칭, 근거."""

    key: str
    name: str
    surfaces: set[str] = field(default_factory=set)
    n_title_docs: int = 0

    def matches(self, text: str) -> str | None:
        """텍스트에 이 사업의 표기가 있으면 그 표기를 돌려준다."""
        flat = re.sub(r"\s+", "", text or "")
        for s in sorted(self.surfaces, key=len, reverse=True):
            if re.sub(r"\s+", "", s) in flat:
                return s
        return None


# 이름 뒤에 붙어 '명사구로 쓰였음'을 보이는 조사·어미
_PARTICLE_AFTER = re.compile(
    r"^(?:은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|부터|까지|이나|나|"
    r"께서|라|란|이라|이란|에는|에도|에서는|으로서|로서|처럼)")
# 이름을 감싸는 부호 — 「사업명」처럼 명시적으로 이름임을 표시한다
_NAME_OPEN = set("「『《【\"'‘“(")
_NAME_CLOSE = set("」』》】\"'’”)")


def named_usage(flat_body: str, name: str) -> int:
    """본문에서 이 이름이 '하나의 이름으로' 쓰인 횟수.

    조사가 바로 붙거나(글로컬대학은/글로컬대학의) 인용부호로 묶인(「글로컬대학」)
    경우만 센다. 제목 줄이 본문에 한 번 되풀이된 것과, 실제로 그 이름으로 부르는
    것을 가르는 신호다. 접미사 목록 없이 등장 맥락만 본다.
    """
    needle = re.sub(r"\s+", "", name or "")
    if not needle or not flat_body:
        return 0
    hits = 0
    i = flat_body.find(needle)
    while i >= 0:
        tail = flat_body[i + len(needle): i + len(needle) + 4]
        head = flat_body[i - 1: i]
        if _PARTICLE_AFTER.match(tail) or head in _NAME_OPEN or tail[:1] in _NAME_CLOSE:
            hits += 1
        i = flat_body.find(needle, i + 1)
    return hits


_ORG_TAIL = re.compile(rf"(?:{_ORG_SUFFIX})$")


def is_organization(name: str) -> bool:
    """이 이름이 기관명인가 — 기관은 사업이 아니다.

    유형 판정은 개체 추출기(_ORG)가 이미 하고 있으므로 그 접미사를 그대로 쓴다.
    사업명용 접미사 목록을 따로 두지 않기 위한 선택이다. 이름부가 길어도
    (대학혁신지원사업 총괄협의회 사무국) 끝이 기관 접미사면 기관으로 본다.
    """
    return bool(_ORG_TAIL.search(re.sub(r"\s+", "", name or "")))


def looks_like_proper_name(name: str) -> bool:
    """고유명 규모의 낱말을 품고 있는가 — 한글 3자 이상 또는 영숫자 3자 이상 토큰.

    "2학기"처럼 기간·수량 표현이 사업으로 올라오는 것을 막는다(실측 운영 DB).
    """
    for w in _TITLE_TOKEN.findall(name or ""):
        if len(re.findall(r"[가-힣]", w)) >= 3:
            return True
        if re.fullmatch(r"[A-Za-z0-9.]{3,}", w):
            return True
    return False


def mine_programs(titles: dict, bodies: dict | None = None) -> dict:
    """제목에서 사업명 후보를 캐고 본문으로 검증한다 — {key: Program}.

    ① 제목을 정리해 어절로 나누고 ② 서로 다른 문서 MIN_CORE_DOCS건 이상에서
    되풀이되는 최장 어구(극대 반복 n-gram)를 모은 뒤 ③ 제목 안 평균 위치가
    앞쪽인 것만 남긴다. 한국어 제목은 '무엇에 관한 것(수식부) + 문서 종류(역할어)'
    순서라, 뒤쪽에 몰리는 어구는 양식·공고·기본계획 같은 역할어다.
    ④ bodies(문서 본문)가 주어지면 본문 뒷받침을 요구한다 — 본문에 나오고,
       충분히 되풀이되며, 조사·인용부호가 붙어 '하나의 이름으로' 불리고,
       기관명이 아니고, 고유명 규모의 낱말을 품은 것만 사업으로 인정한다.
       뒷받침을 못 받으면 만들지 않는다(미상으로 두는 편이 낫다).
    """
    toks = {d: _TITLE_TOKEN.findall(clean_title(t)) for d, t in titles.items()}
    df: Counter = Counter()
    pos: dict = defaultdict(list)
    for _d, ts in toks.items():
        first: dict = {}
        for n in range(1, MAX_TITLE_NGRAM + 1):
            for i in range(len(ts) - n + 1):
                g = " ".join(ts[i : i + n])
                first.setdefault(g, i / max(len(ts), 1))
        for g, p in first.items():
            df[g] += 1
            pos[g].append(p)

    # 낱말별 평균 위치 — 역할어(양식·공고·신청서…)를 이름 목록 없이 가려낸다
    word_pos = {g: sum(ps) / len(ps) for g, ps in pos.items() if " " not in g}

    cands = {g: n for g, n in df.items() if n >= MIN_CORE_DOCS}
    cores: dict = {}
    for g, n in cands.items():
        # 극대성 — 이 어구를 늘려도 문서 수가 줄지 않으면 늘린 쪽이 대표다
        if any(g != h and g in h and cands[h] >= n for h in cands):
            continue
        if sum(pos[g]) / len(pos[g]) > CORE_MAX_POSITION:
            continue                       # 뒤쪽에 몰림 = 문서 역할어
        words = g.split(" ")
        if len(re.sub(r"[\W_]+", "", g)) < 3 or any(re.fullmatch(r"\d{1,2}", w) for w in words):
            continue                       # 숫자 토막("교육부 03")은 사업명이 아니다
        cores[g] = n

    # 사업명 + 문서 역할어로 늘어난 후보는 버린다 — 더 짧은 후보를 품고 있으면서
    # 늘어난 부분에 역할어가 섞인 것("…(HUSS) 사업계획서 양식")
    def has_role_extension(g: str) -> bool:
        for h in cores:
            if h != g and _key_contains(g, h):
                extra = [w for w in g.split(" ") if w not in h.split(" ")]
                if any(word_pos.get(w, 0.0) >= ROLE_WORD_POSITION for w in extra):
                    return True
        return False

    kept = {g: n for g, n in cores.items() if not has_role_extension(g)}

    # 짧은 변형을 긴 대표형으로 흡수 — 문서 수가 크게 줄지 않을 때만
    absorbed: dict = {}
    for g in sorted(kept, key=len):
        for h in sorted(kept, key=len, reverse=True):
            if h != g and _key_contains(h, g) and kept[h] / kept[g] >= ABSORB_RATIO:
                absorbed[g] = h
                break

    programs: dict = {}
    for g, n in sorted(kept.items(), key=lambda x: (-len(x[0]), -x[1])):
        target = absorbed.get(g, g)
        key = program_key(target)
        if not key:
            continue
        p = programs.get(key)
        if p is None:
            programs[key] = Program(key=key, name=target, surfaces={target, g},
                                    n_title_docs=kept.get(target, n))
        else:
            p.surfaces.add(g)
            p.n_title_docs = max(p.n_title_docs, n)
    for p in programs.values():
        p.surfaces |= set(acronyms(p.name))
        # 괄호 약칭을 뗀 표기도 같은 사업이다 ("…양성사업(HUSS)" ↔ "…양성사업")
        bare = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", p.name)).strip()
        if len(re.sub(r"[\W_]+", "", bare)) >= 3:
            p.surfaces.add(bare)
    if bodies is None:
        return programs
    return {k: p for k, p in programs.items() if body_supports_program(p, bodies)}


def body_supports_program(program: Program, bodies: dict) -> bool:
    """본문이 이 후보를 사업으로 뒷받침하는가 — 판정 근거는 전부 본문 통계다."""
    if is_organization(program.name) or not looks_like_proper_name(program.name):
        return False
    flat = re.sub(r"\s+", "", program.name)
    if not flat:
        return False
    occ = [b.count(flat) for b in bodies.values()]
    seen = [o for o in occ if o > 0]
    if len(seen) < PROGRAM_MIN_BODY_DOCS:
        return False                       # 파일 이름에만 있는 어구
    if sum(seen) / len(seen) < PROGRAM_MIN_AVG_MENTIONS:
        return False                       # 한 번 스친 문구지 그 문서의 주제가 아니다
    named = sum(1 for b in bodies.values() if named_usage(b, program.name) > 0)
    return named >= PROGRAM_MIN_NAMED_DOCS


def mine_role_words(titles: dict) -> dict:
    """제목에서 '문서 종류'를 가리키는 낱말을 캔다 — {낱말: 평균 위치}.

    이름 목록을 손으로 적지 않는다. 한국어 제목은 수식부가 앞, 문서 종류가 뒤에
    오므로 제목 안 평균 위치가 뒤쪽인 낱말이 역할어다. 실측 corpus_pilot:
    공고 0.79 · 양식 0.78 · 확약서 0.72 · 기본계획 0.70 · 서식 0.69 · 신청서 0.68.
    """
    pos: dict = defaultdict(list)
    for _d, t in titles.items():
        ts = _TITLE_TOKEN.findall(clean_title(t))
        seen: dict = {}
        for i, w in enumerate(ts):
            seen.setdefault(w, i / max(len(ts), 1))
        for w, p in seen.items():
            pos[w].append(p)
    return {
        w: sum(ps) / len(ps)
        for w, ps in pos.items()
        if len(ps) >= MIN_CORE_DOCS and sum(ps) / len(ps) >= ROLE_WORD_POSITION
        and len(re.sub(r"[\W_]+", "", w)) >= 2
    }


def document_role(filename: str, role_words: dict) -> str:
    """문서의 역할(공고·기본계획·서식 …) — 제목의 역할어 중 가장 뒤에 오는 것.

    역할어 사전은 코퍼스에서 계산된 것(mine_role_words)을 받는다. 사전이 비었거나
    제목에 역할어가 없으면 빈 문자열 — 억지로 이름 붙이지 않는다.
    """
    if not role_words:
        return ""
    ts = _TITLE_TOKEN.findall(clean_title(filename))
    hits = [(i, w) for i, w in enumerate(ts) if w in role_words]
    if not hits:
        return ""
    return max(hits, key=lambda iw: (iw[0], len(iw[1])))[1]


def _key_contains(long_name: str, short_name: str) -> bool:
    """긴 이름이 짧은 이름을 어절 단위로 품고 있는가(공백·괄호 표기 흔들림 무시)."""
    a = re.sub(r"\s+", "", long_name)
    b = re.sub(r"\s+", "", short_name)
    return b in a and a != b


def identify_program(programs: dict, title: str, body: str = "") -> tuple[str, str] | None:
    """문서 하나의 사업 정체 — (key, 근거 문구). 못 정하면 None.

    제목이 1순위다(제목이 곧 문서의 정체). 제목에 없으면 본문 앞부분에서
    MIN_BODY_MENTIONS회 이상 나오는 사업명 중 가장 많이 나온 것을 쓴다.
    """
    t = clean_title(title)
    best = None
    for key, p in programs.items():
        hit = p.matches(t)
        if hit and (best is None or len(hit) > len(best[1])):
            best = (key, hit)
    if best:
        return best
    if not body:
        return None
    flat = re.sub(r"\s+", "", body)
    head = flat[:BODY_HEAD_CHARS]
    scored: list[tuple[int, int, str, str]] = []
    for key, p in programs.items():
        for s in p.surfaces:
            fs = re.sub(r"\s+", "", s)
            if len(fs) < 3:
                continue
            n = flat.count(fs)
            # 본문 어딘가에 나온다고 그 문서의 주제는 아니다. 표지·개요(앞부분)에
            # 한 번은 나와야 정체로 인정한다 — 실측: 다른 장학사업 문서가 본문
            # 뒷부분의 대상 설명만으로 엉뚱한 사업에 묶였다.
            if n >= MIN_BODY_MENTIONS and head.count(fs) >= 1:
                scored.append((n + 3 * head.count(fs), len(fs), key, s))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2], scored[0][3]


# ── 2단계: 유형 있는 개체 언급 ──────────────────────────────────────────────


def extract_typed(text: str) -> dict:
    """텍스트에서 유형별 개체를 뽑는다 — {kind: Counter}. 문맥 통계는 아직 안 쓴다."""
    out: dict = {k: Counter() for k in
                 ("year", "org", "program", "law", "period", "money", "target")}
    for m in _YEAR.finditer(text):
        out["year"][f"{m.group(1)}년"] += 1
    for m in _ORG.finditer(text):
        name = normalize_entity(m.group(0))
        if (name and name not in _GENERIC and not _PLACEHOLDER.match(m.group(1))
                and not _CLAUSE_IN_NAME.search(m.group(1))):
            out["org"][name] += 1
    for m in _PROGRAM.finditer(text):
        name = normalize_entity(m.group())
        if name and name not in _GENERIC:
            out["program"][name] += 1
    for m in _LAW.finditer(text):
        title = (m.group(1) or m.group(4) or "").strip()
        art = m.group(2) or m.group(5)
        sub = m.group(3) or m.group(6)
        if not title or len(title) < 2:
            continue
        name = normalize_entity(title)
        if _CLAUSE_IN_NAME.search(name):
            continue        # 띄어쓰기가 사라져 앞 문장이 법령명에 붙은 경우
        if art:
            name += f" 제{art}조" + (f"의{sub}" if sub else "")
        out["law"][name] += 1
    for m in _PERIOD.finditer(text):
        out["period"][re.sub(r"\s+", "", m.group(0))] += 1
    for m in _MONEY.finditer(text):
        out["money"][re.sub(r"\s+", "", m.group(1))] += 1
    for m in _TARGET.finditer(text):
        out["target"][m.group(1).strip()[:60]] += 1
    return out


def extract_mentions(text: str) -> Counter:
    """(이름, 유형) 언급 횟수 — 이전 계약 유지(사업명은 코퍼스 문맥이 있어야
    정해지므로 여기서는 기관·법령·연도만 낸다)."""
    out: Counter = Counter()
    typed = extract_typed(text)
    for kind in ("year", "org", "program", "law"):
        for name, n in typed[kind].items():
            out[(name, kind)] += n
    return out


# ── 3단계: 코퍼스 통계로 연결 근거가 될 수 없는 개체 걸러내기 ───────────────


def _edit_distance_le1(a: str, b: str) -> bool:
    """편집 거리가 1 이하인가 — 같은 길이의 한 글자 오인식을 잡는다."""
    if a == b:
        return True
    if len(a) != len(b):
        return False
    diff = sum(1 for x, y in zip(a, b) if x != y)
    return diff <= 1


def merge_variants(counts: dict, max_extra: int = 2) -> dict:
    """표기가 거의 같은 개체를 하나로 — {(이름, 유형): 대표 이름}.

    OCR이 앞뒤에 글자를 붙이거나 한 글자를 잘못 읽으면 같은 기관이 둘로 세어진다
    (실측 운영 DB: '한국장학재단' 5문서와 '기한국장학재단' 2문서가 따로 올라왔다).
    특정 낱말을 사전에 넣지 않고 두 가지 일반 규칙만 쓴다.
      · 포함 관계 — 한쪽이 다른 쪽을 품고 길이 차가 max_extra 이하
      · 편집 거리 — 길이가 같고 한 글자만 다르다
    대표형은 언급이 더 많은 표기다. 길이 차가 그보다 크면(의미 있는 복합어,
    예: '국가'우수장학금) 합치지 않아 구별을 보존한다.
    """
    by_kind: dict = {}
    for (name, kind), n in counts.items():
        by_kind.setdefault(kind, {})[name] = by_kind.get(kind, {}).get(name, 0) + n
    canon: dict = {}
    for kind, names in by_kind.items():
        # 언급이 많은 표기를 대표 후보로 먼저 본다
        ordered = sorted(names.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        reps: list[str] = []
        for name, n in ordered:
            hit = None
            for rep in reps:
                if names[rep] < n:
                    continue
                long_, short_ = (name, rep) if len(name) >= len(rep) else (rep, name)
                extra = len(long_) - len(short_)
                if short_ and short_ in long_ and 1 <= extra <= max_extra:
                    hit = rep
                    break
                if _edit_distance_le1(name, rep):
                    hit = rep
                    break
            canon[(name, kind)] = hit or name
            if hit is None:
                reps.append(name)
    return canon


def apply_variants(doc_entities: dict, canon: dict) -> dict:
    """문서별 개체 언급에 표준형을 적용해 다시 합친다."""
    out: dict = {}
    for did, ents in doc_entities.items():
        merged: Counter = Counter()
        for name, kind, n in ents:
            merged[(canon.get((name, kind), name), kind)] += n
        out[did] = [(nm, kd, n) for (nm, kd), n in merged.items()]
    return out


def cohesion(df_compound: int, df_modifier: int) -> float:
    """복합어 응집도 — 수식부가 그 복합어 밖에서 얼마나 흔한가의 역수."""
    return df_compound / max(df_modifier, 1)


def hub_cutoff(n_docs: int) -> int:
    """연결 근거로 쓸 수 있는 최대 문서 수 — √N (코퍼스 크기에 따라 자동)."""
    return max(MIN_CORE_DOCS, int(math.isqrt(max(n_docs, 1))))


def linkable_entities(doc_entities: dict, doc_texts: dict) -> dict:
    """연결 근거로 쓸 수 있는 개체만 남긴다 — {(name, kind): df}.

    · 허브 배제: 문서 √N건 초과 등장
    · 역할어 배제: 기관명 복합어의 응집도가 낮음(수식부가 코퍼스에서 흔함)
    """
    df: Counter = Counter()
    for _did, ents in doc_entities.items():
        for name, kind in {(n, k) for n, k, _ in ents}:
            df[(name, kind)] += 1
    n_docs = max(len(doc_texts), 1)
    cut = hub_cutoff(n_docs)

    # 수식부 문서 빈도 — 복합어 응집도 계산용
    mod_df: Counter = Counter()
    modifiers = {}
    for (name, kind) in df:
        if kind != "org":
            continue
        m = re.match(rf"^(.+?)({_ORG_SUFFIX})$", name)
        if m and len(m.group(1)) >= 2:
            modifiers[(name, kind)] = m.group(1)
    if modifiers:
        wanted = set(modifiers.values())
        for _did, text in doc_texts.items():
            flat = re.sub(r"\s+", "", text or "")
            for w in wanted:
                if w in flat:
                    mod_df[w] += 1

    keep: dict = {}
    for (name, kind), n in df.items():
        if kind == "year":
            continue                       # 달력상 우연은 주제 연관이 아니다
        if n < MIN_LINK_DOCS or n > cut:
            continue
        mod = modifiers.get((name, kind))
        if mod and cohesion(n, mod_df.get(mod, n)) < MIN_COHESION:
            continue                       # 역할 명칭(평가위원회·운영위원회 …)
        keep[(name, kind)] = n
    return keep


# ── 4단계: DB 적재 ──────────────────────────────────────────────────────────


def _doc_text(db, d: dict) -> str:
    if d.get("doc_type") == "regulation":
        parts = [c["content"] for c in db.chunks_for_docs([d["id"]])]
        parts.append(d.get("filename") or "")
        return "\n".join(parts)
    return d.get("masked_text") or ""


def corpus_profile(db) -> dict:
    """코퍼스 전체의 개체·정체 프로필 — 그래프와 적재가 함께 쓴다.

    반환: {
      "programs":  {key: Program},
      "identity":  {doc_id: (program_key, 근거 문구)},
      "typed":     {doc_id: {kind: Counter}},
      "linkable":  {(name, kind): df},
      "titles":    {doc_id: 정리된 제목},
    }
    """
    docs = [d for d in db.list_documents() if d.get("doc_type") != "ocr"]
    titles = {d["id"]: d.get("filename") or "" for d in docs}
    texts = {d["id"]: _doc_text(db, d) for d in docs}
    # 본문은 공백을 지운 형태로 한 번만 만들어 사업 검증·정체 판정에 함께 쓴다
    flat_bodies = {i: re.sub(r"\s+", "", t) for i, t in texts.items()}
    programs = mine_programs(titles, flat_bodies)

    typed: dict = {}
    identity: dict = {}
    for d in docs:
        did = d["id"]
        typed[did] = extract_typed(texts[did][:BODY_HEAD_CHARS * 4])
        got = identify_program(programs, titles[did], texts[did])
        if got:
            identity[did] = got

    # 패턴으로 주운 사업명(_PROGRAM)은 제목에서 사업 정체를 캐지 못했을 때만 쓴다.
    # 코퍼스가 갖춰지면 '정부재정지원사업·입학전형' 같은 범주어가 섞여 들어온다.
    known = {re.sub(r"\s+", "", s) for p in programs.values() for s in p.surfaces}
    use_pattern_programs = not programs

    def _keep_program(name: str) -> bool:
        return use_pattern_programs or re.sub(r"\s+", "", name) in known

    doc_entities = {
        did: [(name, kind, n)
              for kind in ("org", "program", "law")
              for name, n in t[kind].items()
              if kind != "program" or _keep_program(name)]
        for did, t in typed.items()
    }
    # 표기 변형 통합 — OCR이 앞뒤 글자를 붙인 같은 기관이 둘로 세어지지 않게
    global_counts: Counter = Counter()
    for ents in doc_entities.values():
        for name, kind, n in ents:
            global_counts[(name, kind)] += n
    doc_entities = apply_variants(doc_entities, merge_variants(global_counts))
    linkable = linkable_entities(doc_entities, texts)
    return {
        "programs": programs,
        "identity": identity,
        "typed": typed,
        "linkable": linkable,
        "titles": {d: clean_title(t) for d, t in titles.items()},
        "doc_entities": doc_entities,
        "role_words": mine_role_words(titles),
    }


def extract_doc_entities(db, min_mentions: int = 1) -> dict:
    """전체 문서의 개체를 추출해 DB에 교체 저장한다.

    저장 대상은 '연결 근거가 될 수 있는' 개체다 — 사업 정체(program)와, 허브·역할어를
    걸러낸 기관(org)·근거 법령(law). 연도·금액·기간은 문서 속성이라 그래프에 올리지
    않는다(같은 해라는 이유로 문서를 잇지 않기 위해서다).
    반환: 요약 집계.
    """
    prof = corpus_profile(db)
    linkable = prof["linkable"]
    n_docs = 0
    n_links = 0
    dropped = 0
    for did, ents in prof["doc_entities"].items():
        mentions = [(n, k, c) for n, k, c in ents
                    if (n, k) in linkable and c >= min_mentions]
        dropped += len(ents) - len(mentions)
        ident = prof["identity"].get(did)
        if ident:
            prog = prof["programs"][ident[0]]
            mentions.append((prog.name, "program", 1))
        db.replace_doc_entities(did, mentions)
        n_docs += 1
        n_links += len(mentions)
    return {
        "docs": n_docs, "links": n_links,
        "programs": len(prof["programs"]),
        "identified": len(prof["identity"]),
        "dropped_mentions": dropped,
    }


def canonical_map(counts: dict) -> dict:
    """전역 표준형 병합 규칙(일반) — (이름,유형)별 총 언급수로 대표형을 정한다.

    같은 유형에서 A가 B의 접미이고 길이차가 1~2(군더더기 접두, 예: OCR이 붙인
    '기'한국장학재단)면서 A의 근거(언급수)가 B 이상이면 B를 A로 병합한다.
    """
    by_kind: dict = {}
    for (name, kind), n in counts.items():
        by_kind.setdefault(kind, {})[name] = by_kind.get(kind, {}).get(name, 0) + n
    canon: dict = {}
    for kind, names in by_kind.items():
        ordered = sorted(names.items(), key=lambda kv: (len(kv[0]), -kv[1]))
        bases: list = []
        for name, n in ordered:
            hit = None
            for b in bases:
                if name != b and name.endswith(b) and 1 <= len(name) - len(b) <= 2 \
                        and names[b] >= n:
                    hit = b
                    break
            canon[(name, kind)] = hit or name
            if hit is None:
                bases.append(name)
    return canon


__all__ = [
    "Program", "clean_title", "program_key", "acronyms",
    "mine_programs", "mine_role_words", "document_role",
    "identify_program", "extract_typed", "extract_mentions",
    "linkable_entities", "hub_cutoff", "cohesion", "corpus_profile",
    "merge_variants", "apply_variants", "named_usage", "is_organization",
    "looks_like_proper_name", "body_supports_program",
    "extract_doc_entities", "normalize_entity", "canonical_map",
]
