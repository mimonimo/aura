"""검증기 2종 — 배점 커버리지 (W1-W2 TASK-08, 브리프 6장 4번).

공고의 배점 항목별로 초안 반영 여부를 기계적으로 체크하고 누락을 보고한다.
1차 판정은 키워드 매칭(결정론). 임베딩 유사도 보조는 검색 스택이 서면 추가하되,
최종 판단은 항상 사람 몫으로 리포트만 낸다 (architecture §5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    name: str
    points: int
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class CoverageReport:
    total_points: int
    covered_points: int
    covered: list[Criterion]
    missing: list[Criterion]


def check_coverage(criteria: list[dict], draft: str) -> CoverageReport:
    """배점 항목 목록(dict: name/points/keywords)과 초안을 대조한다."""
    covered: list[Criterion] = []
    missing: list[Criterion] = []
    for c in criteria:
        crit = Criterion(name=c["name"], points=int(c["points"]), keywords=tuple(c["keywords"]))
        if draft and any(kw in draft for kw in crit.keywords):
            covered.append(crit)
        else:
            missing.append(crit)
    return CoverageReport(
        total_points=sum(c.points for c in covered) + sum(c.points for c in missing),
        covered_points=sum(c.points for c in covered),
        covered=covered,
        missing=missing,
    )
