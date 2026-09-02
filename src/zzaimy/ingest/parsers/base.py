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
class ParsedImage:
    """문서에서 추출된 그림 한 장 — path는 파서 산출물 디렉터리의 파일."""

    page_no: int  # 1부터
    path: Path


@dataclass(frozen=True)
class ParsedEntry:
    """읽기 순서상의 한 항목 — 원본 좌표(bbox)까지 보존한다.

    kind: text | heading | table | image. table/image는 ref로
    ParseResult.tables / images 목록의 인덱스를 가리킨다.
    """

    page_no: int
    kind: str
    text: str = ""
    ref: int = -1
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class ParseResult:
    parser: str
    elapsed_s: float
    pages: list[ParsedPage] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    entries: list[ParsedEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 줄 단위 OCR 좌표 (MinerU middle.json) — 스캔 복원 뷰의 투명 레이어 재료
    ocr_lines: list[dict] = field(default_factory=list)
    ocr_page_sizes: dict[int, tuple[float, float]] = field(default_factory=dict)


class DocumentParser(Protocol):
    """bake-off 대상 파서가 구현해야 하는 인터페이스."""

    name: str

    def parse(self, path: Path) -> ParseResult: ...
