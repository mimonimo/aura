"""파서 공통 인터페이스 (W1-W2 TASK-04).

입력: 파일 경로 / 출력: 페이지별 텍스트 + 표 구조 + 레이아웃 메타.
MinerU·Docling 어댑터가 이 형태로 결과를 정규화해야 bake-off 채점이
파서 중립적으로 이뤄진다. 판정 기준 1순위는 표 구조 보존이다 (브리프 8.1-A).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TableCell:
    """표 셀 하나. row/col은 0부터, span은 병합 크기(기본 1)."""

    row: int
    col: int
    text: str
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False


@dataclass(frozen=True)
class ParsedTable:
    page_no: int  # 1부터
    n_rows: int
    n_cols: int
    cells: tuple[TableCell, ...]

    @property
    def merged_cells(self) -> tuple[TableCell, ...]:
        return tuple(c for c in self.cells if c.row_span > 1 or c.col_span > 1)


@dataclass(frozen=True)
class ParsedPage:
    page_no: int  # 1부터
    text: str


@dataclass(frozen=True)
class ParseResult:
    parser: str
    elapsed_s: float
    pages: list[ParsedPage] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DocumentParser(Protocol):
    """bake-off 대상 파서가 구현해야 하는 인터페이스."""

    name: str

    def parse(self, path: Path) -> ParseResult: ...
