"""Docling 어댑터 (W1-W2 TASK-04).

docling 패키지는 parsers 추가 의존성(`pip install -e ".[parsers]"`)이며
import를 parse() 안으로 미뤄 미설치 환경에서도 모듈 로드는 가능하게 한다.
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

from zzaimy.ingest.parsers.base import ParsedPage, ParsedTable, ParseResult, TableCell


def _convert_table(item, page_no: int) -> ParsedTable:
    cells = []
    for c in item.data.table_cells:
        cells.append(
            TableCell(
                row=c.start_row_offset_idx,
                col=c.start_col_offset_idx,
                row_span=c.end_row_offset_idx - c.start_row_offset_idx,
                col_span=c.end_col_offset_idx - c.start_col_offset_idx,
                text=(c.text or "").strip(),
                is_header=bool(getattr(c, "column_header", False)),
            )
        )
    return ParsedTable(
        page_no=page_no,
        n_rows=item.data.num_rows,
        n_cols=item.data.num_cols,
        cells=tuple(cells),
    )


class DoclingParser:
    name = "docling"

    def parse(self, path: Path) -> ParseResult:
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import TableItem, TextItem

        t0 = time.perf_counter()
        doc = DocumentConverter().convert(str(path)).document
        elapsed = time.perf_counter() - t0

        page_texts: dict[int, list[str]] = defaultdict(list)
        tables: list[ParsedTable] = []
        warnings: list[str] = []
        for item, _level in doc.iterate_items():
            page_no = item.prov[0].page_no if getattr(item, "prov", None) else 1
            if isinstance(item, TableItem):
                tables.append(_convert_table(item, page_no))
            elif isinstance(item, TextItem):
                page_texts[page_no].append(item.text)

        n_pages = max([*page_texts.keys(), *(t.page_no for t in tables), 0])
        pages = [
            ParsedPage(page_no=i, text="\n".join(page_texts.get(i, [])))
            for i in range(1, n_pages + 1)
        ]
        return ParseResult(
            parser=self.name, elapsed_s=elapsed, pages=pages, tables=tables, warnings=warnings
        )
