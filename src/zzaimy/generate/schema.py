"""공고 정형 스키마 (W1-W2 TASK-08, 브리프 6장 1번).

공고 파싱 결과에서 LLM(guided_json)으로 추출하는 목표 구조.
xgrammar 스키마 강제로 형식을 보장하고, 추출값의 원문 대조로 내용을 보장한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SectionSpec(BaseModel):
    """계획서 목차의 한 섹션."""

    name: str
    requirements: str = ""  # 이 섹션이 다뤄야 할 요구사항 요약


class CriterionSpec(BaseModel):
    """평가지표 한 항목."""

    name: str
    points: int = Field(ge=0)
    keywords: list[str] = Field(default_factory=list)


class AnnouncementSchema(BaseModel):
    """공고 1건의 정형 스키마 — 날실."""

    title: str
    sections: list[SectionSpec]
    criteria: list[CriterionSpec]
    budget_limit_krw: int | None = None
