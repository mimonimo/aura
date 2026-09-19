"""PII 탐지·마스킹 파이프라인 (W1-W2 TASK-05, 브리프 9장 risks / 절대 규칙 3).

Presidio 기반 한국형 recognizer 골격. 인덱싱 **이전** 단계에 위치해야 하며,
이를 타입으로 강제한다: 인덱싱 계층은 `MaskedDocument`만 받도록 선언하고,
`MaskedDocument`는 이 모듈의 `PiiMasker.mask()`를 통해서만 생성된다.

현재 recognizer 6종 (표본 문서 확인 후 확장 — 미해결 질문 #11 관련):
- KR_RRN            주민등록번호 (체크섬 검증)
- KR_PHONE          휴대전화·유선전화
- EMAIL             이메일
- KR_BRN            사업자등록번호 (체크섬 검증)
- KR_BANK_ACCOUNT   계좌번호 (문맥 라벨 필수 — 오탐 방지)
- KR_STUDENT_ID     학번·수험번호·사번 (라벨 문맥 필수)
- KR_BIRTHDATE      생년월일 (라벨 문맥 필수 — 작성일자 같은 일반 날짜는 제외)
- KR_NAME           성명 (라벨 문맥 기반. 자유 문장 속 성명은 못 잡는다 →
                    NER 기반 확장은 표본 확인 후 결정)

알려진 한계 (골격 단계):
- 2020-10 이후 발급분 주민등록번호는 체크섬 규칙이 적용되지 않는다.
  현재는 체크섬 유효분만 잡는다. 표본 확인 후 완화 여부를 결정한다.
- 성명은 "성명:", "담당자:" 등 라벨 뒤에 오는 경우만 탐지한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import spacy
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import SpacyNlpEngine

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 0.4


# --- 파이프라인 위치를 강제하는 타입 ---


@dataclass(frozen=True)
class RawDocument:
    """마스킹 전 문서. 인덱싱 계층에 넘길 수 없다."""

    doc_id: str
    text: str


@dataclass(frozen=True)
class MaskedDocument:
    """마스킹을 거친 문서. 인덱싱 계층은 이 타입만 받는다."""

    doc_id: str
    text: str


@dataclass(frozen=True)
class MaskEvent:
    """감사 로그 항목 — 무엇이 어디서 가려졌는지. 원문 텍스트는 절대 담지 않는다."""

    doc_id: str
    entity_type: str
    start: int
    end: int


# --- 체크섬 검증 recognizer ---


class RrnRecognizer(PatternRecognizer):
    """주민등록번호 — 패턴 + 체크섬 검증."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="KR_RRN",
            supported_language="ko",
            name="kr_rrn",
            patterns=[Pattern("rrn", r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)", 0.5)],
        )

    def validate_result(self, pattern_text: str) -> bool:
        digits = [int(c) for c in pattern_text if c.isdigit()]
        if len(digits) != 13:
            return False
        weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
        s = sum(d * w for d, w in zip(digits[:12], weights))
        return (11 - s % 11) % 10 == digits[12]


class BrnRecognizer(PatternRecognizer):
    """사업자등록번호 — 하이픈 표기 + 체크섬 검증."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="KR_BRN",
            supported_language="ko",
            name="kr_brn",
            patterns=[Pattern("brn", r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)", 0.5)],
        )

    def validate_result(self, pattern_text: str) -> bool:
        digits = [int(c) for c in pattern_text if c.isdigit()]
        if len(digits) != 10:
            return False
        weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
        s = sum(d * w for d, w in zip(digits[:9], weights))
        s += (digits[8] * 5) // 10
        return (10 - s % 10) % 10 == digits[9]


def _label_alt(labels: tuple[str, ...]) -> str:
    """라벨 대안식 — 글자 사이 공백을 허용한다.

    한글 서식(표 칸)은 '성 명', '학 번', '생 년 월 일'처럼 글자를 띄어 쓰는 일이 흔하고,
    OCR도 라벨 안에 공백을 끼워 넣는다. 실측(2026-09-15, .hwp 이수보고서): '성 명 ○○○'이
    '성명:' 패턴에 안 걸려 이름이 그대로 저장됐다. 라벨 목록은 서식 일반어이지 특정 문서가 아니다.
    """
    import re as _re

    return "|".join(r"\s*".join(_re.escape(ch) for ch in lab) for lab in labels)


# 라벨 뒤 구분자 — 공백 또는 콜론·표 칸 경계(|)가 하나는 있어야 한다. 콜론을 필수로 두면
# 표 서식을 전부 놓치고, 아무것도 요구하지 않으면 '이름으로 변경'·'성명은'처럼 라벨에
# 조사가 붙은 문장에서 뒤 낱말을 값으로 잡는다(잔여 검사 오탐 실측 2026-09-15).
_LABEL_SEP = r"(?:\s{1,3}[:：|]?\s{0,3}|\s{0,3}[:：|]\s{0,3})"
# 라벨 뒤에 흔히 오는 '값이 아닌' 서식 낱말 — 성명 후보에서 제외(일반 서식 어휘)
_NOT_A_NAME = (
    "연락처", "전화", "휴대전화", "이메일", "소속", "부서", "직위", "직급", "서명", "날인",
    "주소", "생년월일", "학번", "학과", "성별", "확인", "서명란", "인적사항", "정보",
)


def _pattern_recognizer(entity: str, name: str, regex: str, score: float) -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity=entity,
        supported_language="ko",
        name=name,
        patterns=[Pattern(name, regex, score)],
    )


def _build_recognizers() -> list[PatternRecognizer]:
    return [
        RrnRecognizer(),
        BrnRecognizer(),
        _pattern_recognizer(
            "KR_PHONE",
            "kr_phone",
            # 휴대전화(01x) 또는 지역번호 유선전화. 날짜(YYYY-MM-DD)와 겹치지 않도록
            # 맨 앞 0을 요구하고, 경계는 '영숫자'로 건다 — 16진수 해시·파일명·코드처럼
            # 글자와 숫자가 이어진 토큰 속 숫자열은 전화번호가 아니다(잔여 검사 오탐 실측).
            r"(?<![0-9A-Za-z])0(?:1[016789][-. ]?\d{3,4}|\d{1,2}[-. ]?\d{3,4})[-. ]?\d{4}(?![0-9A-Za-z])",
            0.6,
        ),
        _pattern_recognizer(
            "EMAIL",
            "email",
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            0.9,
        ),
        _pattern_recognizer(
            "KR_BANK_ACCOUNT",
            "kr_bank_account",
            # 계좌 문맥 라벨이 바로 앞에 있을 때만 탐지한다 (오탐 방지).
            # presidio는 regex 모듈을 쓰므로 가변 길이 lookbehind가 허용된다.
            r"(?<=(?:계좌번호|입금계좌|가상계좌|계좌)\s{0,5}[:：]?\s{0,5})"
            r"\d{2,6}[- ]\d{2,6}[- ]\d{2,11}(?!\d)",
            0.6,
        ),
        # 성명 — 라벨 문맥 기반(자유 문장 속 성명은 NER 도입 전까지 못 잡는다). 두 갈래:
        #  · '성명·이름'처럼 이름 자체를 뜻하는 라벨은 띄어쓰기('성 명')·콜론 없음(표 칸)도 허용
        #  · '담당자·신청인'처럼 문장에도 흔한 역할 명사는 콜론·칸 경계가 있을 때만
        #    ("신청인 자격은 …" 같은 문장에서 뒤 낱말을 이름으로 잡던 오탐 실측)
        _pattern_recognizer(
            "KR_NAME",
            "kr_name_field",
            r"(?<=(?:" + _label_alt(("성명", "이름", "학생명", "성함", "대표자명", "담당자명"))
            + r")" + _LABEL_SEP + r")"
            r"(?!(?:" + "|".join(_NOT_A_NAME) + r")(?![가-힣]))"
            r"[가-힣]{2,4}(?![가-힣])",
            0.6,
        ),
        _pattern_recognizer(
            "KR_NAME",
            "kr_name_role",
            r"(?<=(?:" + _label_alt(("담당자", "책임자", "작성자", "신청인", "신청자", "지원자",
                                     "대표자", "연구책임자"))
            + r")\s{0,3}[:：|]\s{0,3})"
            r"(?!(?:" + "|".join(_NOT_A_NAME) + r")(?![가-힣]))"
            r"[가-힣]{2,4}(?![가-힣])",
            0.6,
        ),
        _pattern_recognizer(
            "KR_STUDENT_ID",
            "kr_student_id",
            # 학번·수험번호·사번 — 대학 서식의 개인 식별자. 라벨 문맥이 있을 때만.
            r"(?<=(?:" + _label_alt(("학번", "수험번호", "사번", "응시번호", "군번"))
            + r")" + _LABEL_SEP + r")"
            r"\d{5,10}(?![0-9A-Za-z])",
            0.6,
        ),
        _pattern_recognizer(
            "KR_BIRTHDATE",
            "kr_birthdate",
            # 생년월일 라벨 뒤의 날짜. 일반 날짜(작성일자 등)는 라벨이 달라 잡지 않는다.
            r"(?<=(?:" + _label_alt(("생년월일", "출생일", "생일")) + r")" + _LABEL_SEP + r")"
            r"(?:19|20)\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?",
            0.6,
        ),
    ]


class _BlankKoreanNlpEngine(SpacyNlpEngine):
    """토크나이즈만 하는 빈 한국어 파이프라인.

    recognizer가 전부 정규식 기반이라 NER 모델이 필요 없다.
    NER 확장 시 이 엔진을 교체한다.
    """

    def __init__(self) -> None:
        super().__init__(models=[{"lang_code": "ko", "model_name": "blank"}])

    def load(self) -> None:
        nlp = spacy.blank("xx")
        # 파서·NER 없는 빈 파이프라인이라 길이 제한을 올려도 안전하다.
        # (기본 100만 자 — 대형 행정 문서에서 초과 사례 실측됨)
        nlp.max_length = 4_000_000
        self.nlp = {"ko": nlp}  # type: ignore[assignment]  # 상위 클래스가 None으로 선언


# --- 마스커 ---


@dataclass
class PiiMasker:
    """문서 텍스트에서 PII를 탐지해 `[엔티티명]` 토큰으로 치환한다."""

    _analyzer: AnalyzerEngine = field(init=False, repr=False)

    def __post_init__(self) -> None:
        registry = RecognizerRegistry(supported_languages=["ko"])
        for rec in _build_recognizers():
            registry.add_recognizer(rec)
        self._analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=_BlankKoreanNlpEngine(),
            supported_languages=["ko"],
        )

    def mask(self, doc: RawDocument) -> tuple[MaskedDocument, list[MaskEvent]]:
        results = self._analyzer.analyze(
            text=doc.text, language="ko", score_threshold=SCORE_THRESHOLD
        )
        # 겹치는 탐지는 앞선 것·긴 것 우선으로 병합
        spans: list[tuple[int, int, str]] = []
        for r in sorted(results, key=lambda r: (r.start, -(r.end - r.start))):
            if spans and r.start < spans[-1][1]:
                continue
            spans.append((r.start, r.end, r.entity_type))

        parts: list[str] = []
        cursor = 0
        events: list[MaskEvent] = []
        for start, end, entity in spans:
            parts.append(doc.text[cursor:start])
            parts.append(f"[{entity}]")
            events.append(MaskEvent(doc_id=doc.doc_id, entity_type=entity, start=start, end=end))
            cursor = end
        parts.append(doc.text[cursor:])

        for ev in events:
            logger.info(
                "masked doc=%s entity=%s span=%d..%d", ev.doc_id, ev.entity_type, ev.start, ev.end
            )
        return MaskedDocument(doc_id=doc.doc_id, text="".join(parts)), events

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        results = self._analyzer.analyze(text=text, language="ko", score_threshold=SCORE_THRESHOLD)
        spans: list[tuple[int, int, str]] = []
        for r in sorted(results, key=lambda r: (r.start, -(r.end - r.start))):
            if spans and r.start < spans[-1][1]:
                continue
            spans.append((r.start, r.end, r.entity_type))
        return spans

    def tokenize(self, text: str) -> tuple[str, dict[str, str]]:
        """PII 를 되돌릴 수 있는 토큰으로 치환한다. 같은 값은 같은 토큰을 쓴다.

        반환 vault(토큰→원값)는 교내에만 두고 밖으로 보내지 않는다. 외부 모델에는 토큰본만 보낸다.
        """
        vault: dict[str, str] = {}
        val2tok: dict[str, str] = {}
        counters: dict[str, int] = {}
        parts: list[str] = []
        cursor = 0
        for start, end, entity in self._spans(text):
            val = text[start:end]
            tok = val2tok.get(val)
            if tok is None:
                counters[entity] = counters.get(entity, 0) + 1
                tok = f"[[{entity}_{counters[entity]}]]"
                val2tok[val] = tok
                vault[tok] = val
            parts.append(text[cursor:start])
            parts.append(tok)
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts), vault

    @staticmethod
    def restore(text: str, vault: dict[str, str]) -> str:
        """토큰을 원값으로 되돌린다(결정론적 치환). 값 복원은 생성이 아니라 정확 치환으로만 한다."""
        # 긴 토큰부터 치환해 부분 겹침을 피한다
        for tok in sorted(vault, key=len, reverse=True):
            text = text.replace(tok, vault[tok])
        return text
