"""조각 품질 판정 — 검색 단위가 될 자격을 일반 규칙으로 가린다.

문제(실측 2026-09-19, corpus_pilot 1,846조각): 48.2%가 실질 글자수 40자 미만이고,
그중 20자 미만 681건은 코퍼스 중심 벡터와의 코사인이 0.762로 긴 조각(0.679~0.70)보다
높다. 중심에 가깝다는 것은 어떤 질의에도 딸려 온다는 뜻이고, 실제로 무작위 질의 800건
상위10 노출 횟수가 평균 5.45회(200자 이상 4.26회, 20~30자 3.15회)로 가장 잦았다.
"연관성이 억지로 끼워진" 결과의 상당수가 이 파편들이다.

설계 원칙
  · 특정 문서·문장에 맞춘 예외를 두지 않는다. 모든 규칙은 한국어 행정문서의 일반
    형태이거나, 코퍼스에서 계산되는 통계(문서 빈도)다.
  · 기본은 버리기가 아니라 표시(flag)다. 호출부가 강도(Strictness)를 고른다.
  · 판정에는 항상 이유가 붙는다 — 화면·로그에서 "왜 뺐는지"를 댈 수 있어야 한다.

쓰는 곳
  · 검색 후보 정리(regulations.select_candidates) — SEARCH 강도
  · 인제스트 직후 색인 대상 선별(pipeline) — INDEX 강도
  · 데이터 공방 화면의 조각 품질 표시(data_explorer) — FLAG 강도
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import IntEnum

# ── 이유 코드 ────────────────────────────────────────────────────────────────
# (코드, 화면 문구) — 화면에 그대로 나가므로 완결된 명사구로 쓴다.
REASON_LABELS: dict[str, str] = {
    "too_short": "실질 내용 부족",
    "thin": "본문이 짧음",
    "symbol_only": "기호·구분선만 있음",
    "page_number": "페이지 번호만 있음",
    "toc_line": "목차·색인 줄",
    "question_only": "질문만 있고 답이 없음",
    "duplicate": "같은 문서 안 중복",
    "boilerplate": "여러 문서에 반복되는 정형 문구",
    "header_footer": "머리말·꼬리말 반복",
    "ocr_damage": "OCR 손상 의심",
    "low_text": "문자 비율 비정상",
}

# ── 경계값 ──────────────────────────────────────────────────────────────────
# 실질 글자수 = 한글·영문·숫자만 센 길이(공백·구두점·표 기호 제외).

# 20자 미만은 코퍼스 중심 코사인 0.762 / 상위10 노출 5.45회로 측정상 '아무 질의에나
# 딸려오는' 구간이다(20~30자 0.722 / 3.15회). 그 아래만 확실한 배제 대상으로 둔다.
MIN_SUBSTANTIVE_DROP = 20
# 40자 미만은 문장 종결(…다./…함./…음.)을 포함한 비율이 1.5~6.2%로, 단독으로 하나의
# 진술을 담지 못한다(40자 이상 구간은 10~30%). 배제까지는 않고 '얇음'으로 표시한다.
MIN_SUBSTANTIVE_THIN = 40
# 청킹이 목표로 삼는 최소 실질 길이 — 이 아래면 이웃과 합친다(버리지 않는다).
MERGE_MIN_SUBSTANTIVE = 40

# 글자(한글·영문·숫자)가 전체의 이 비율 미만이면 기호 나열 — 표 괘선·불릿만 남은 조각.
MIN_WORD_CHAR_RATIO = 0.35
# 실질 글자의 대부분이 숫자면 표 껍데기(값만 남고 항목명이 사라진 표)로 본다.
# 한글이 적다는 이유로는 걸지 않는다 — 영문 본문은 정상이다(실측 오탐 1건).
MAX_DIGIT_RATIO = 0.75

# 정형 문구(보일러플레이트) 판정 — 문자 12-gram이 이 '문서 묶음' 수 이상에서 공통으로
# 나타나면 어느 문서에나 붙는 말로 본다. 문서별 하드코딩 대신 코퍼스 통계.
# 묶음으로 세는 이유: 같은 문서가 여러 번 적재되면(실측 corpus_pilot: HUSS 공고문이
# 3건) 그 본문 전체가 '여러 문서에 반복'으로 잡혀 실제 내용이 통째로 사라진다.
# 최소 묶음 수와 묶음 비율 중 큰 쪽을 기준으로 삼는다 — 코퍼스가 커질수록 '몇 건
# 겹쳤나'가 아니라 '몇 %에서 겹쳤나'가 정형 문구의 기준이 되어야 한다.
# 실측(corpus_pilot 묶음 114개): 3~5묶음 기준은 같은 사업의 요건 설명처럼 실제
# 내용까지 걷어냈고(127·58·21건), 5%(=6묶음)부터는 47개 혁신기획서 표지에 똑같이
# 붙은 확약 문구처럼 진짜 정형 문구만 남았다(15건).
BOILERPLATE_MIN_DOCS = 3
BOILERPLATE_DF_RATIO = 0.05
BOILERPLATE_SHINGLE = 12
# 조각의 12-gram 중 이 비율 이상이 공통이면 정형 문구로 판정.
BOILERPLATE_RATIO = 0.60
# 문서 묶음 — 12-gram 자카드(minhash 추정)가 이 값 이상이면 같은 문서로 본다.
DOC_GROUP_JACCARD = 0.60
# 문서 묶음 서명에서 제외할 n-gram — 문서의 이 비율을 넘겨 나타나는 것은 변별력이
# 없다. 이걸 빼지 않으면 '대부분이 공용 서식인 문서'끼리 한 묶음이 되어 그 서식이
# 정형 문구로 잡히지 않는다.
DOC_SIGNATURE_DF_RATIO = 0.5
_MINHASH_K = 128
# 머리말·꼬리말 — 같은 줄이 한 문서 안에서 이 횟수 이상 반복되면 쪽 장식으로 본다.
HEADER_FOOTER_MIN_REPEAT = 3
# 근사 중복 — 12-gram 자카드가 이 값 이상이면 같은 내용으로 본다.
NEAR_DUP_JACCARD = 0.90

# OCR 손상 — 띄어쓰기가 사라진 한글 연속 길이. 정상 한국어 복합명사는 길어야
# 12~14자('산업체위탁교육과정')라 그 위를 의심 구간으로 둔다.
OCR_RUN_LEN = 16
# 신호는 모두 '비율'로 잰다 — 긴 조각에서 한 번 걸린 것으로 문서 전체를 손상으로
# 몰지 않기 위해서다(실측: 1만 자 조각이 긴 낱말 하나로 손상 판정을 받았다).
OCR_RUN_RATIO = 0.10        # 한글 글자 중 띄어쓰기 없는 긴 연속에 속한 비율
OCR_SYLLABLE_RATIO = 0.25   # 한 글자 토큰이 셋 이상 잇따른 구간에 속한 비율 = 글자 단위 분해
OCR_SYLLABLE_MIN_TOKENS = 12  # 이보다 짧은 조각은 비율을 재지 않는다(자간 띄운 제목 오탐)
OCR_COLLAPSE_PER_KCHAR = 0.5  # 1,000자당 날짜 구분자 소실 횟수
# 손상 신호가 이 개수 이상이면 배제 대상으로 올린다(하나뿐이면 표시만).
OCR_SIGNALS_TO_DROP = 2

_WORD_CHAR = re.compile(r"[가-힣A-Za-z0-9]")
_HANGUL = re.compile(r"[가-힣]")
_DIGIT = re.compile(r"[0-9]")
_WS = re.compile(r"\s+")
# 목차 줄 — 점선 리더(····, ……) 또는 줄 끝에 쪽번호만 달린 표제
_DOT_LEADER = re.compile(r"[.·․‧⋯…]{3,}\s*\d{1,4}\s*$|[.·․‧⋯…]{5,}")
_TRAILING_PAGE = re.compile(r"^\s*\S.{0,58}?[ \t.·…]{2,}\d{1,4}\s*$")
# 페이지 번호 단독 — "12", "- 12 -", "12 / 34", "p.12"
_PAGE_ONLY = re.compile(r"^[\s\-–—·.()\[\]/]*(?:p\.?\s*)?\d{1,4}\s*(?:/\s*\d{1,4})?[\s\-–—·.()\[\]/]*$", re.I)
# 표 괘선·구분선만
_RULE_ONLY = re.compile(r"^[\s|+\-–—─━═_.·*~<>]+$")
# 한글 의문 종결 — '…나요?', '…습니까', '…인가요', '…는지요' 등. 물음표가 없어도 잡는다.
_QUESTION_END = re.compile(
    r"(?:\?|？"
    r"|(?:나요|가요|까요|ㄹ까|은가|는가|습니까|ㅂ니까|인지요|는지요|나요|건가요)\s*[.?!]?)\s*$"
)
_SENT_SPLIT = re.compile(r"(?<=[.?!？])\s+|\n+")
# 선언형 종결 — 한국어 행정문서의 평서 종결
_DECLARATIVE_END = re.compile(
    r"(?:다|함|음|임|됨|요|것|오|정|함|중)\s*[.]\s*$"
    r"|(?:니다|습니다|입니다|합니다|됩니다)\s*[.]?\s*$"
    r"|(?:함|음|임|됨|다)\s*$"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"https?://|www\.")
# OCR 날짜 붕괴 — 구분자가 사라져 숫자가 통으로 붙은 형태("202692(수)")
_DATE_COLLAPSE = re.compile(r"\d{5,}\s*[(（][월화수목금토일][)）]|[(（][월화수목금토일][)）]\s*~\s*\d{3,}")
_HANGUL_RUN = re.compile(r"[가-힣]+")


class Strictness(IntEnum):
    """필터 강도 — 호출부가 고른다.

    FLAG   이유만 붙이고 아무것도 버리지 않는다(화면 표시용, 기본값).
    SEARCH 검색 근거로 쓸 수 없는 조각만 뺀다(운영 검색 경로).
    INDEX  색인 단계 — SEARCH 기준 + 얇은 조각·손상 의심까지 뺀다.
    """

    FLAG = 0
    SEARCH = 1
    INDEX = 2


# 이유별 최소 강도 — 이 강도 이상에서 조각이 빠진다.
_DROP_AT: dict[str, Strictness] = {
    "too_short": Strictness.SEARCH,
    "symbol_only": Strictness.SEARCH,
    "page_number": Strictness.SEARCH,
    "toc_line": Strictness.SEARCH,
    "question_only": Strictness.SEARCH,
    "duplicate": Strictness.SEARCH,
    "boilerplate": Strictness.SEARCH,
    "header_footer": Strictness.SEARCH,
    "ocr_damage": Strictness.SEARCH,   # 손상 신호 2개 이상일 때만 이 이유가 붙는다
    "thin": Strictness.INDEX,
    "low_text": Strictness.INDEX,
}


@dataclass(frozen=True)
class Verdict:
    """조각 한 건의 판정 — 점수·이유·통과 여부."""

    score: float
    reasons: tuple[str, ...] = ()
    detail: dict = field(default_factory=dict)

    def keep(self, level: Strictness | int = Strictness.SEARCH) -> bool:
        level = Strictness(int(level))
        return not any(_DROP_AT.get(r, Strictness.INDEX) <= level for r in self.reasons)

    @property
    def labels(self) -> list[str]:
        return [REASON_LABELS.get(r, r) for r in self.reasons]

    def why(self) -> str:
        """화면·로그용 한 줄 — 이유가 없으면 빈 문자열."""
        return " · ".join(self.labels)


# ── 기본 계측 ────────────────────────────────────────────────────────────────


def substantive_len(text: str) -> int:
    """실질 글자수 — 한글·영문·숫자만. 공백·구두점·표 기호는 세지 않는다."""
    return len(_WORD_CHAR.findall(text or ""))


def normalize(text: str) -> str:
    """비교용 정규화 — 공백을 하나로 줄이고 앞뒤를 자른다."""
    return _WS.sub(" ", (text or "")).strip()


def _fingerprint(text: str) -> str:
    """중복 비교 키 — 공백·구두점을 모두 지운 글자열(표기 흔들림 흡수)."""
    return "".join(_WORD_CHAR.findall(text or ""))


def _shingles(text: str, k: int = BOILERPLATE_SHINGLE) -> set[str]:
    s = _fingerprint(text)
    if len(s) < k:
        return {s} if s else set()
    return {s[i : i + k] for i in range(len(s) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


# ── 단일 조각 규칙 (문맥 없이 판정 가능한 것) ────────────────────────────────


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _is_toc(text: str) -> bool:
    """목차·색인 — 점선 리더나 '표제 + 쪽번호' 줄이 조각의 과반을 차지할 때.

    긴 본문 안에 점선이 한 번 나온 것으로는 판정하지 않는다(실측: 4만 자 조각이
    내부의 점선 하나로 목차 판정을 받았다). 줄 단위 비율로 본다.
    """
    lines = _lines(text)
    if not lines:
        return False
    hits = sum(1 for ln in lines if _DOT_LEADER.search(ln) or _TRAILING_PAGE.match(ln))
    if len(lines) == 1:
        return hits == 1 and substantive_len(text) < MIN_SUBSTANTIVE_THIN
    return hits >= max(2, (len(lines) + 1) // 2)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(normalize(text)) if s.strip()]


def _is_question_only(text: str) -> bool:
    """질문만 있고 답이 없는 조각 — FAQ 질문줄이 검색 단위로 떨어진 경우.

    모든 문장이 의문형일 때만 해당한다. "하나라도 의문형이면"으로 잡으면 긴 본문이
    우연히 걸린다(실측: 1만 자 조각이 이 규칙에 걸렸다).
    """
    sents = _sentences(text)
    if not sents:
        return False
    q = sum(1 for s in sents if _QUESTION_END.search(s))
    return q == len(sents)


def _table_cell_text(text: str) -> str | None:
    """표 조각 JSON 이면 칸 글자를 공백으로 이어 돌려준다. 표가 아니면 None."""
    t = (text or "").lstrip()
    if not t.startswith("{") or '"cells"' not in t[:400]:
        return None
    try:
        import json

        data = json.loads(t)
    except ValueError:
        return None
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        return None
    return " ".join(str(c[-1]) for c in cells if isinstance(c, list) and c)


def ocr_damage_signals(text: str) -> list[str]:
    """OCR 손상 신호 목록 — 일반 지표만 쓴다(문서별 규칙 없음).

    신호: ① 날짜·숫자 구분자 소실 ② 띄어쓰기 소실(긴 한글 연속)
          ③ 글자 단위 분해(한 글자 토큰 과다)
    모두 길이로 나눈 비율이라 조각 길이에 휘둘리지 않는다.
    """
    out: list[str] = []
    # 표 조각(JSON)은 칸의 글자만 본다. 표 칸에는 '계'·'예'·'○' 같은 한 글자 값이 원래 많아
    # '글자 단위 분해'를 재면 멀쩡한 디지털 표가 손상으로 몰린다(실측 2026-09-20: 적재 점검
    # 108건 중 87건이 이 오탐, .hwpx 원문 표 포함). 표에는 ①② 신호만 쓴다.
    cells = _table_cell_text(text)
    is_table = cells is not None
    if is_table:
        text = cells
    n = max(len(text or ""), 1)
    collapses = len(_DATE_COLLAPSE.findall(text or ""))
    if collapses and collapses / (n / 1000.0) >= OCR_COLLAPSE_PER_KCHAR:
        out.append("날짜 구분자 소실")
    runs = _HANGUL_RUN.findall(text or "")
    total_hangul = sum(len(r) for r in runs)
    long_hangul = sum(len(r) for r in runs if len(r) >= OCR_RUN_LEN)
    if total_hangul >= 40 and long_hangul / total_hangul >= OCR_RUN_RATIO:
        out.append("띄어쓰기 소실")
    # 글자 단위 분해 — 한 글자 토큰이 '연달아' 이어지는 구간만 센다. 개조식 공문은 '및'·'등'·'각'
    # 같은 한 글자 낱말이 흩어져 많아 단순 비율로는 멀쩡한 문서가 손상으로 몰렸다(실측
    # 2026-09-20: 적재 점검 오탐 다수). 진짜 분해는 '첨 단 신 소 재'처럼 셋 이상 잇따른다.
    words = [w for w in normalize(text).split(" ") if w]
    hangul_tokens = [w for w in words if _HANGUL.fullmatch(w)]
    if len(hangul_tokens) >= OCR_SYLLABLE_MIN_TOKENS and not is_table:
        in_runs = run = 0
        for w in words:
            if len(w) == 1 and _HANGUL.fullmatch(w):
                run += 1
                continue
            in_runs += run if run >= 3 else 0
            run = 0
        in_runs += run if run >= 3 else 0
        if in_runs / len(hangul_tokens) >= OCR_SYLLABLE_RATIO:
            out.append("글자 단위 분해")
    return out


def base_reasons(text: str) -> tuple[list[str], dict]:
    """문맥 없이 판정되는 이유와 계측값."""
    raw = text or ""
    stripped = raw.strip()
    sub = substantive_len(stripped)
    total = len(normalize(stripped)) or 1
    word_ratio = sub / total
    digits = len(_DIGIT.findall(stripped))
    detail = {
        "substantive": sub,
        "word_ratio": round(word_ratio, 3),
        "digit_ratio": round(digits / max(sub, 1), 3),
    }
    reasons: list[str] = []

    if not stripped:
        return ["too_short"], detail
    if _PAGE_ONLY.match(stripped):
        reasons.append("page_number")
    if _RULE_ONLY.match(stripped):
        reasons.append("symbol_only")
    elif word_ratio < MIN_WORD_CHAR_RATIO:
        reasons.append("symbol_only")
    if _is_toc(stripped):
        reasons.append("toc_line")
    if sub < MIN_SUBSTANTIVE_DROP:
        reasons.append("too_short")
    elif sub < MIN_SUBSTANTIVE_THIN:
        reasons.append("thin")
    if _is_question_only(stripped):
        reasons.append("question_only")
    signals = ocr_damage_signals(stripped)
    if signals:
        detail["ocr_signals"] = signals
        if len(signals) >= OCR_SIGNALS_TO_DROP:
            reasons.append("ocr_damage")
    if sub >= MIN_SUBSTANTIVE_THIN and detail["digit_ratio"] >= MAX_DIGIT_RATIO:
        reasons.append("low_text")     # 숫자만 남은 표 껍데기 (영문 본문은 정상으로 본다)
    return reasons, detail


def _score(sub: int, reasons: list[str]) -> float:
    """0~1 품질 점수 — 길이로 기본점을 주고 이유마다 깎는다(정렬·표시용)."""
    base = min(1.0, sub / 200.0)          # 200자 실질이면 만점
    penalty = {
        "too_short": 0.6, "symbol_only": 0.6, "page_number": 0.6, "toc_line": 0.5,
        "question_only": 0.4, "duplicate": 0.5, "boilerplate": 0.5,
        "header_footer": 0.5, "ocr_damage": 0.3, "thin": 0.2, "low_text": 0.2,
    }
    for r in reasons:
        base *= 1.0 - penalty.get(r, 0.1)
    return round(max(0.0, min(1.0, base)), 3)


def assess(text: str, meta: dict | None = None) -> Verdict:
    """조각 한 건 판정 — 코퍼스 문맥이 필요한 규칙(중복·정형 문구)은 빼고.

    meta로 코퍼스 패스가 미리 계산한 이유를 넘길 수 있다:
      {"extra_reasons": ["boilerplate", ...]}
    """
    reasons, detail = base_reasons(text)
    for r in (meta or {}).get("extra_reasons", ()):
        if r not in reasons:
            reasons.append(r)
    return Verdict(score=_score(detail["substantive"], reasons),
                   reasons=tuple(reasons), detail=detail)


def is_useful(text: str, meta: dict | None = None,
              level: Strictness | int = Strictness.SEARCH) -> tuple[bool, str]:
    """(쓸 만한가, 버릴 이유) — 이유는 화면에 그대로 쓸 수 있는 문구."""
    v = assess(text, meta)
    return v.keep(level), v.why()


def score_chunk(text: str, meta: dict | None = None) -> float:
    """조각 품질 점수 0~1 — 낮을수록 검색 단위로서 가치가 없다."""
    return assess(text, meta).score


# ── 코퍼스 규칙 (여러 조각을 함께 봐야 판정되는 것) ─────────────────────────


def _doc_id(chunk: dict) -> object:
    return chunk.get("doc_id", chunk.get("document_id", None))


def _text(chunk: dict) -> str:
    return chunk.get("content") or chunk.get("text") or ""


def _minhash(shingles: set[str], k: int = _MINHASH_K) -> tuple[int, ...]:
    """집합의 최소 해시 k개 — 자카드 추정용 서명(외부 의존 없이)."""
    if not shingles:
        return ()
    hs = sorted(hash(s) & 0xFFFFFFFF for s in shingles)
    return tuple(hs[:k])


def _minhash_jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    union = sorted(sa | sb)[: min(len(a), len(b))]
    if not union:
        return 0.0
    return sum(1 for h in union if h in sa and h in sb) / len(union)


def document_groups(chunks: list[dict]) -> dict:
    """같은 내용의 문서를 한 묶음으로 — {doc_id: 대표 doc_id}.

    같은 파일이 이름만 달리해 여러 번 적재되면(실측 corpus_pilot: 같은 공고문 3건)
    그 본문이 '여러 문서에 반복'으로 오인돼 통째로 빠진다. 문서 단위 근사 중복을
    먼저 묶어 두고, 정형 문구는 '서로 다른 묶음'에서 반복될 때만 인정한다.
    """
    per_doc: dict = {}
    for did, texts in _by_doc(chunks).items():
        sh: set[str] = set()
        for t in texts:
            sh |= _shingles(t)
        per_doc[did] = sh
    # 문서 절반 이상에 나오는 n-gram은 '같은 문서인가'를 가릴 힘이 없다(공용 서식·
    # 동의문). 그런 조각만으로 문서가 묶이면 정작 그 정형 문구를 찾지 못한다.
    df: Counter = Counter()
    for sh in per_doc.values():
        df.update(sh)
    limit = max(2, int(len(per_doc) * DOC_SIGNATURE_DF_RATIO))
    sigs: dict = {}
    for did, sh in per_doc.items():
        distinctive = {g for g in sh if df[g] <= limit}
        sigs[did] = _minhash(distinctive or sh)   # 남는 게 없으면 원본으로
    rep: dict = {}
    reps: list = []
    for did in sorted(sigs, key=lambda d: (d is None, d)):
        hit = None
        for r in reps:
            if _minhash_jaccard(sigs[did], sigs[r]) >= DOC_GROUP_JACCARD:
                hit = r
                break
        rep[did] = hit if hit is not None else did
        if hit is None:
            reps.append(did)
    return rep


def boilerplate_shingles(chunks: list[dict],
                         min_docs: int | None = None,
                         groups: dict | None = None) -> set[str]:
    """서로 다른 문서 묶음 min_docs개 이상에 공통으로 나타나는 문자 n-gram 집합.

    개인정보 수집·이용 동의문, 제출 서류 안내처럼 어느 공고에나 똑같이 붙는
    문구를 문서별 규칙 없이 찾아낸다 — 문서 빈도만 본다.
    """
    rep = groups if groups is not None else document_groups(chunks)
    per_group: dict = {}
    for did, texts in _by_doc(chunks).items():
        g = rep.get(did, did)
        s = per_group.setdefault(g, set())
        for t in texts:
            s |= _shingles(t)
    df: Counter = Counter()
    for s in per_group.values():
        df.update(s)
    cut = min_docs if min_docs is not None else boilerplate_cutoff(len(per_group))
    return {g for g, n in df.items() if n >= cut}


def boilerplate_cutoff(n_groups: int) -> int:
    """정형 문구로 인정할 최소 묶음 수 — 최소 건수와 비율 중 큰 쪽."""
    import math

    return max(BOILERPLATE_MIN_DOCS, math.ceil(n_groups * BOILERPLATE_DF_RATIO))


def _by_doc(chunks: list[dict]) -> dict:
    out: dict = {}
    for c in chunks:
        out.setdefault(_doc_id(c), []).append(_text(c))
    return out


def _header_footer_lines(texts: list[str]) -> set[str]:
    """한 문서 안에서 같은 줄이 반복되면 머리말·꼬리말로 본다."""
    counts: Counter = Counter()
    for t in texts:
        for ln in _lines(t):
            key = _fingerprint(ln)
            if 2 <= len(key) <= 40:
                counts[key] += 1
    return {k for k, n in counts.items() if n >= HEADER_FOOTER_MIN_REPEAT}


def corpus_reasons(chunks: list[dict],
                   boilerplate: set[str] | None = None) -> dict:
    """조각 목록 전체를 보고 판정되는 이유 — {조각 인덱스: [이유,...]}.

    · duplicate     같은 문서(묶음) 안 중복·근사 중복 — 실질이 긴 쪽을 남긴다
    · boilerplate   서로 다른 문서 묶음에 반복되는 정형 문구
    · header_footer 같은 문서 안에서 반복되는 줄로만 이루어진 조각
    boilerplate 집합을 넘기면 그 코퍼스 기준을 쓴다(부분 집합만 판정할 때).
    """
    out: dict = {}

    def add(i: int, reason: str) -> None:
        out.setdefault(i, [])
        if reason not in out[i]:
            out[i].append(reason)

    rep = document_groups(chunks)
    common = (boilerplate if boilerplate is not None
              else boilerplate_shingles(chunks, groups=rep))

    # 정형 문구 — 조각의 n-gram 중 공통 비율
    for i, c in enumerate(chunks):
        sh = _shingles(_text(c))
        if len(sh) >= 3 and len(sh & common) / len(sh) >= BOILERPLATE_RATIO:
            add(i, "boilerplate")

    # 문서 단위 규칙 — 머리말·꼬리말은 한 문서 안의 반복으로 본다
    by_doc: dict = {}
    for i, c in enumerate(chunks):
        by_doc.setdefault(_doc_id(c), []).append(i)
    for _did, idxs in by_doc.items():
        repeated = _header_footer_lines([_text(chunks[i]) for i in idxs])
        for i in idxs:
            lines = [ln for ln in (_fingerprint(x) for x in _lines(_text(chunks[i]))) if ln]
            if lines and all(ln in repeated for ln in lines):
                add(i, "header_footer")

    # 중복·근사 중복 — 문서 묶음 단위(같은 파일이 여러 번 적재된 경우까지)
    by_group: dict = {}
    for i, c in enumerate(chunks):
        did = _doc_id(c)
        by_group.setdefault(rep.get(did, did), []).append(i)
    for idxs in by_group.values():
        order = sorted(idxs, key=lambda i: -substantive_len(_text(chunks[i])))
        kept: list[tuple[str, set[str]]] = []
        for i in order:
            fp = _fingerprint(_text(chunks[i]))
            if not fp:
                continue
            sh = _shingles(_text(chunks[i]))
            dup = False
            for kfp, ksh in kept:
                if fp == kfp or fp in kfp or _jaccard(sh, ksh) >= NEAR_DUP_JACCARD:
                    dup = True
                    break
            if dup:
                add(i, "duplicate")
            else:
                kept.append((fp, sh))
    return out


def assess_all(chunks: list[dict],
               boilerplate: set[str] | None = None) -> list[Verdict]:
    """조각 목록 전체 판정 — 단일 규칙 + 코퍼스 규칙을 합친다."""
    extra = corpus_reasons(chunks, boilerplate)
    return [
        assess(_text(c), {"extra_reasons": extra.get(i, [])})
        for i, c in enumerate(chunks)
    ]


def filter_chunks(chunks: list[dict], level: Strictness | int = Strictness.SEARCH,
                  boilerplate: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(남길 조각, 뺀 조각) — 뺀 조각에는 `quality_reasons`·`quality_why`가 붙는다."""
    verdicts = assess_all(chunks, boilerplate)
    keep: list[dict] = []
    drop: list[dict] = []
    for c, v in zip(chunks, verdicts):
        item = dict(c)
        item["quality_score"] = v.score
        item["quality_reasons"] = list(v.reasons)
        item["quality_why"] = v.why()
        (keep if v.keep(level) else drop).append(item)
    return keep, drop


__all__ = [
    "Strictness", "Verdict", "REASON_LABELS",
    "assess", "assess_all", "is_useful", "score_chunk",
    "substantive_len", "normalize", "ocr_damage_signals",
    "boilerplate_shingles", "corpus_reasons", "filter_chunks", "document_groups",
    "MERGE_MIN_SUBSTANTIVE", "MIN_SUBSTANTIVE_DROP", "MIN_SUBSTANTIVE_THIN",
]
