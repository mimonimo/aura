"""HTML 표 → 셀 그리드 변환 (W1-W2 TASK-04).

MinerU는 표를 HTML로 내보낸다. rowspan/colspan을 반영해 셀을 그리드
좌표에 배치해야 표 구조 보존 여부를 Docling 결과와 같은 기준으로
채점할 수 있다. 표준 라이브러리 html.parser만 쓴다.
"""

from __future__ import annotations

from html.parser import HTMLParser

from zzaimy.ingest.parsers.base import ParsedTable, TableCell


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cells: list[TableCell] = []
        self._row = -1
        self._occupied: set[tuple[int, int]] = set()  # rowspan이 점유한 좌표
        self._current: dict | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in ("td", "th"):
            attr = dict(attrs)
            col = self._col
            while (self._row, col) in self._occupied:
                col += 1
            row_span = int(attr.get("rowspan") or 1)
            col_span = int(attr.get("colspan") or 1)
            self._current = {
                "row": self._row,
                "col": col,
                "row_span": row_span,
                "col_span": col_span,
                "is_header": tag == "th",
            }
            self._text_parts = []
            for dr in range(row_span):
                for dc in range(col_span):
                    self._occupied.add((self._row + dr, col + dc))
            self._col = col + col_span

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._current is not None:
            self.cells.append(
                TableCell(text="".join(self._text_parts).strip(), **self._current)
            )
            self._current = None


def parse_html_table(html: str, page_no: int) -> ParsedTable:
    """HTML `<table>` 하나를 ParsedTable로 변환한다."""
    parser = _TableHTMLParser()
    parser.feed(html)
    cells = tuple(parser.cells)
    n_rows = max((c.row + c.row_span for c in cells), default=0)
    n_cols = max((c.col + c.col_span for c in cells), default=0)
    return ParsedTable(page_no=page_no, n_rows=n_rows, n_cols=n_cols, cells=cells)
