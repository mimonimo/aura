"""파서 bake-off 채점 지표 테스트 (W1-W2 TASK-04).

채점 기준을 코드로 만든다 — 주관 평가가 아니라 재현 가능한 지표로.
모든 데이터는 합성이다.
"""

from zzaimy.ingest.parsers.base import ParsedPage, ParsedTable, ParseResult, TableCell
from zzaimy.ingest.parsers.metrics import GroundTruth, GroundTruthTable, score


def cell(row, col, text, *, row_span=1, col_span=1, header=False):
    return TableCell(
        row=row, col=col, row_span=row_span, col_span=col_span, text=text, is_header=header
    )


def make_truth():
    return GroundTruth(
        n_pages=2,
        page_texts={1: ["사업 개요 문단"], 2: ["기대 효과 문단"]},
        tables=[
            GroundTruthTable(
                page_no=1,
                n_rows=2,
                n_cols=2,
                header_texts=["사업명", "연도"],
                cell_texts=["사업명", "연도", "합성사업A", "2023"],
                n_merged_cells=0,
            )
        ],
    )


def perfect_result():
    return ParseResult(
        parser="perfect",
        elapsed_s=1.0,
        pages=[
            ParsedPage(page_no=1, text="사업 개요 문단"),
            ParsedPage(page_no=2, text="기대 효과 문단"),
        ],
        tables=[
            ParsedTable(
                page_no=1,
                n_rows=2,
                n_cols=2,
                cells=(
                    cell(0, 0, "사업명", header=True),
                    cell(0, 1, "연도", header=True),
                    cell(1, 0, "합성사업A"),
                    cell(1, 1, "2023"),
                ),
            )
        ],
    )


def test_perfect_parse_scores_full_marks():
    s = score(perfect_result(), make_truth())
    assert s.table_count_match
    assert s.row_col_accuracy == 1.0
    assert s.header_recall == 1.0
    assert s.cell_text_recall == 1.0
    assert s.text_miss_rate == 0.0
    assert s.page_order_preserved


def test_missing_table_detected():
    r = perfect_result()
    broken = ParseResult(parser=r.parser, elapsed_s=r.elapsed_s, pages=r.pages, tables=[])
    s = score(broken, make_truth())
    assert not s.table_count_match
    assert s.cell_text_recall == 0.0


def test_lost_cells_lower_recall():
    r = perfect_result()
    t = r.tables[0]
    half = ParsedTable(page_no=1, n_rows=1, n_cols=2, cells=t.cells[:2])
    s = score(
        ParseResult(parser=r.parser, elapsed_s=r.elapsed_s, pages=r.pages, tables=[half]),
        make_truth(),
    )
    assert s.cell_text_recall == 0.5
    assert s.row_col_accuracy < 1.0


def test_missing_page_text_raises_miss_rate():
    r = perfect_result()
    pages = [ParsedPage(page_no=1, text="사업 개요 문단"), ParsedPage(page_no=2, text="")]
    s = score(
        ParseResult(parser=r.parser, elapsed_s=r.elapsed_s, pages=pages, tables=r.tables),
        make_truth(),
    )
    assert s.text_miss_rate == 0.5


def test_swapped_pages_break_order():
    r = perfect_result()
    pages = [
        ParsedPage(page_no=1, text="기대 효과 문단"),
        ParsedPage(page_no=2, text="사업 개요 문단"),
    ]
    s = score(
        ParseResult(parser=r.parser, elapsed_s=r.elapsed_s, pages=pages, tables=r.tables),
        make_truth(),
    )
    assert not s.page_order_preserved


def test_nbsp_and_spacing_variants_still_match():
    # CID 폰트 PDF는 공백이 \xa0(NBSP)로 추출된다 — 정규화 없이는 전부 빗나간다
    r = perfect_result()
    pages = [
        ParsedPage(page_no=1, text="사업\xa0개요\xa0문단"),
        ParsedPage(page_no=2, text="기대  효과 문단"),
    ]
    t = r.tables[0]
    cells = tuple(
        TableCell(
            row=c.row, col=c.col, row_span=c.row_span, col_span=c.col_span,
            text=c.text.replace(" ", "\xa0"), is_header=c.is_header,
        )
        for c in t.cells
    )
    nbsp_table = ParsedTable(page_no=1, n_rows=2, n_cols=2, cells=cells)
    s = score(
        ParseResult(parser=r.parser, elapsed_s=r.elapsed_s, pages=pages, tables=[nbsp_table]),
        make_truth(),
    )
    assert s.text_miss_rate == 0.0
    assert s.cell_text_recall == 1.0
    assert s.page_order_preserved


def test_merged_cell_preservation_counted():
    truth = GroundTruth(
        n_pages=1,
        page_texts={1: []},
        tables=[
            GroundTruthTable(
                page_no=1,
                n_rows=2,
                n_cols=2,
                header_texts=[],
                cell_texts=["병합제목", "좌", "우"],
                n_merged_cells=1,
            )
        ],
    )
    good = ParseResult(
        parser="p",
        elapsed_s=0.1,
        pages=[ParsedPage(page_no=1, text="")],
        tables=[
            ParsedTable(
                page_no=1,
                n_rows=2,
                n_cols=2,
                cells=(
                    cell(0, 0, "병합제목", col_span=2),
                    cell(1, 0, "좌"),
                    cell(1, 1, "우"),
                ),
            )
        ],
    )
    flat = ParseResult(
        parser="p",
        elapsed_s=0.1,
        pages=good.pages,
        tables=[
            ParsedTable(
                page_no=1,
                n_rows=2,
                n_cols=2,
                cells=(
                    cell(0, 0, "병합제목"),
                    cell(0, 1, ""),
                    cell(1, 0, "좌"),
                    cell(1, 1, "우"),
                ),
            )
        ],
    )
    assert score(good, truth).merged_cell_recall == 1.0
    assert score(flat, truth).merged_cell_recall == 0.0
