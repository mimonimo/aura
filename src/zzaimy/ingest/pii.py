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
            # 맨 앞 0을 요구하고 숫자 경계를 건다.
            r"(?<!\d)0(?:1[016789][-. ]?\d{3,4}|\d{1,2}[-. ]?\d{3,4})[-. ]?\d{4}(?!\d)",
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
        _pattern_recognizer(
            "KR_NAME",
            "kr_name",
            # 라벨 문맥 기반. 자유 문장 속 성명은 NER 도입 전까지 못 잡는다.
            r"(?<=(?:성명|이름|담당자|책임자|작성자|신청인)\s{0,3}[:：]\s{0,3})"
            r"[가-힣]{2,4}(?![가-힣])",
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
        self.nlp = {"ko": spacy.blank("xx")}  # type: ignore[assignment]  # 상위 클래스가 None으로 선언


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
