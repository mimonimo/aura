"""MinerU HTML 표 → 셀 그리드 변환 테스트 (W1-W2 TASK-04)."""

from zzaimy.ingest.parsers.html_table import parse_html_table


def test_simple_table_grid():
    html = (
        "<table><tr><th>사업명</th><th>연도</th></tr>"
        "<tr><td>합성사업A</td><td>2023</td></tr></table>"
    )
    t = parse_html_table(html, page_no=1)
    assert t.n_rows == 2
    assert t.n_cols == 2
    texts = {c.text for c in t.cells}
    assert texts == {"사업명", "연도", "합성사업A", "2023"}


def test_th_cells_are_headers():
    html = "<table><tr><th>항목</th></tr><tr><td>값</td></tr></table>"
    t = parse_html_table(html, page_no=1)
    by_text = {c.text: c for c in t.cells}
    assert by_text["항목"].is_header
    assert not by_text["값"].is_header


def test_colspan_is_preserved():
    html = (
        "<table><tr><td colspan='2'>병합제목</td></tr>"
        "<tr><td>좌</td><td>우</td></tr></table>"
    )
    t = parse_html_table(html, page_no=1)
    merged = next(c for c in t.cells if c.text == "병합제목")
    assert merged.col_span == 2
    assert t.n_cols == 2


def test_rowspan_shifts_following_cells():
    # rowspan 셀 아래 행의 셀은 그 열을 건너뛰어 배치돼야 한다
    html = (
        "<table><tr><td rowspan='2'>세로병합</td><td>r1c2</td></tr>"
        "<tr><td>r2c2</td></tr></table>"
    )
    t = parse_html_table(html, page_no=1)
    by_text = {c.text: c for c in t.cells}
    assert by_text["세로병합"].row_span == 2
    assert by_text["r2c2"].col == 1  # 0열은 병합 셀이 차지
    assert t.n_rows == 2
    assert t.n_cols == 2
