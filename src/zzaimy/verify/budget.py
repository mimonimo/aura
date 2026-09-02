"""검증기 3종 — 예산 계산 (W1-W2 TASK-08, 절대 규칙 5).

예산 계산에 LLM을 쓰지 않는다. 정수 원 단위 + Decimal 비율의 결정론 로직만.
"""

from __future__ import annotations

import re
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


_AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_UNIT = r"(?:조|억|천만|백만|십만|만|천|백|십)?"
# "750백만 원(250만 원 × 24명 × 1개 학기)" — 총액(계산식) 패턴
_CALC_LINE = re.compile(
    rf"({_AMOUNT})\s*({_UNIT})\s*원\s*\(([^()]*[×xX*][^()]*)\)"
)
_FACTOR = re.compile(rf"({_AMOUNT})\s*({_UNIT})")


def _value(num: str, unit: str) -> Decimal:
    from zzaimy.verify.numbers import _UNIT_VALUE

    return Decimal(num.replace(",", "")) * _UNIT_VALUE.get(unit or "", 1)


def _fmt_krw(v: Decimal) -> str:
    iv = int(v)
    if iv and iv % 10**4 == 0:
        return f"{iv // 10**4:,}만"
    return f"{iv:,}"


def audit_budget_lines(text: str) -> list[str]:
    """초안 속 '총액(단가 × 수량 …)' 계산식을 결정론으로 재검산한다.

    LLM이 예산을 계산하지 못하게 하는 절대 규칙 5의 집행 장치 — 표기된
    총액과 인수들의 곱이 다르면 불일치로 보고한다.
    """
    issues: list[str] = []
    for m in _CALC_LINE.finditer(text):
        total = _value(m.group(1), m.group(2))
        product = Decimal(1)
        n_factors = 0
        for f in _FACTOR.finditer(m.group(3)):
            product *= _value(f.group(1), f.group(2))
            n_factors += 1
        if n_factors < 2 or product == 0:
            continue
        if product != total:
            issues.append(
                f"'{m.group(0)}' — 재계산 {_fmt_krw(product)} 원"
                f" ≠ 표기 {_fmt_krw(total)} 원"
            )
    return issues


def compute_budget(items: list[BudgetItem], limit_krw: int | None = None) -> BudgetTable:
    rows = [BudgetRow(item=i, subtotal_krw=i.unit_price_krw * i.quantity) for i in items]
    return BudgetTable(rows=rows, total_krw=sum(r.subtotal_krw for r in rows), limit_krw=limit_krw)
