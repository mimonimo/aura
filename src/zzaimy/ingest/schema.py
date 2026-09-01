"""문서 4계열 분류와 계열별 메타데이터 스키마 (W1-W2 TASK-06, 브리프 5.6).

4계열은 단일 인덱스로 통합하지 않는다 — 계열별로 처리 경로가 분리되며,
그 분기의 기준이 이 스키마다. ④ 지출 계열은 분류는 하되 초기 범위에서
제외한다(절대 규칙 10): 분류기가 ④로 판정한 문서는 파이프라인 밖으로 보낸다.

열람 등급(AccessLevel)은 미해결 질문 #7(값 체계 미정) 상태지만 자리를 지금
만든다 — 나중에 붙이면 전체 재인덱싱이다. 값은 문자열이라 벡터DB 메타데이터
필터에 그대로 쓸 수 있고, 미분류 문서는 가장 보수적인 RESTRICTED가 기본이다.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocSeries(str, Enum):
    """브리프 1장의 문서 4계열."""

    ANNOUNCEMENT = "announcement"  # ① 공고·지침류
    PROPOSAL = "proposal"  # ② 사업계획서
    PROGRAM_REPORT = "program_report"  # ③ 프로그램 계획서/결과보고서
    EXPENDITURE = "expenditure"  # ④ 지출 문서 — 초기 범위 제외


class AccessLevel(str, Enum):
    """문서 열람 등급 — 잠정값. 실제 값 체계는 미해결 질문 #7 확정 후 교체.

    검색 단계 필터로 쓰인다(절대 규칙 4). 미분류는 RESTRICTED로 두어
    등급 확정 전 문서가 검색 결과로 새지 않게 한다.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class BaseDocMeta(BaseModel):
    """전 계열 공통 메타데이터."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    source_name: str  # 원본 파일명 (경로 제외 — 서버 경로를 메타데이터에 남기지 않는다)
    access_level: AccessLevel = AccessLevel.RESTRICTED
    year: int | None = None
    business_name: str | None = None  # 사업명


class AnnouncementMeta(BaseDocMeta):
    """① 공고·지침류 — 요구사항 정의의 원천."""

    series: Literal[DocSeries.ANNOUNCEMENT] = DocSeries.ANNOUNCEMENT
    agency: str | None = None  # 주관 기관
    deadline: date | None = None  # 제출 기한


class ProposalMeta(BaseDocMeta):
    """② 사업계획서 — 논리 구조·표현 자산 + Writer 학습 데이터."""

    series: Literal[DocSeries.PROPOSAL] = DocSeries.PROPOSAL
    selected: bool | None = None  # 선정 여부. 미상이면 None — 지어내지 않는다


class ProgramReportMeta(BaseDocMeta):
    """③ 프로그램 계획서/결과보고서 — 실적 카드의 원천, 최우선 가치."""

    series: Literal[DocSeries.PROGRAM_REPORT] = DocSeries.PROGRAM_REPORT
    program_name: str | None = None


class ExpenditureMeta(BaseDocMeta):
    """④ 지출 문서 — 초기 범위 제외. 분류·격리 목적으로만 존재한다."""

    series: Literal[DocSeries.EXPENDITURE] = DocSeries.EXPENDITURE


DocMeta = AnnouncementMeta | ProposalMeta | ProgramReportMeta | ExpenditureMeta


# --- 규칙 기반 계열 분류기 ---
# 파일명 키워드로 시작한다(TASK-06 지시). 문서 구조 기반 규칙은 파서 확정(TASK-09)
# 후 표본을 보고 추가한다. 규칙은 순서가 있다: 앞선 규칙이 이긴다.
# "프로그램 계획서"(③)가 "사업계획서"(②)보다 먼저 와야 하는 이유가 그것이다.

_RULES: list[tuple[re.Pattern[str], DocSeries]] = [
    (re.compile(r"지출|정산|구매|결의"), DocSeries.EXPENDITURE),
    (re.compile(r"프로그램|결과보고"), DocSeries.PROGRAM_REPORT),
    (re.compile(r"공고|지침|평가기준"), DocSeries.ANNOUNCEMENT),
    (re.compile(r"사업계획서|계획서"), DocSeries.PROPOSAL),
]


def classify_series(filename: str) -> DocSeries | None:
    """파일명으로 문서 계열을 판정한다. 판정 불가면 None — 추측하지 않는다."""
    for pattern, series in _RULES:
        if pattern.search(filename):
            return series
    return None
