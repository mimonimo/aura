"""검증기 3종 — 예산 계산 (W1-W2 TASK-08, 절대 규칙 5).

예산 계산에 LLM을 쓰지 않는다. 정수 원 단위 + Decimal 비율의 결정론 로직만.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class BudgetItem:
    name: str
    unit_price_krw: int
    quantity: int

    def __post_init__(self) -> None:
        if self.unit_price_krw < 0 or self.quantity < 0:
            raise ValueError(f"예산 항목에 음수 불가: {self.name}")


@dataclass(frozen=True)
class BudgetRow:
    item: BudgetItem
    subtotal_krw: int


@dataclass(frozen=True)
class BudgetTable:
    rows: list[BudgetRow] = field(default_factory=list)
    total_krw: int = 0
    limit_krw: int | None = None

    @property
    def within_limit(self) -> bool:
        return self.limit_krw is None or self.total_krw <= self.limit_krw

    @property
    def usage_ratio(self) -> Decimal | None:
        if not self.limit_krw:
            return None
        return Decimal(self.total_krw) / Decimal(self.limit_krw)


def compute_budget(items: list[BudgetItem], limit_krw: int | None = None) -> BudgetTable:
    rows = [BudgetRow(item=i, subtotal_krw=i.unit_price_krw * i.quantity) for i in items]
    return BudgetTable(rows=rows, total_krw=sum(r.subtotal_krw for r in rows), limit_krw=limit_krw)
