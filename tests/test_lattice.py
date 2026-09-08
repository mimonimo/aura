"""괘선 직독 표 추출 — 합성 PDF로 격자·병합·셀 텍스트·열 폭 검증."""

from __future__ import annotations

import pytest

from zzaimy.ingest.parsers.lattice import extract_tables


@pytest.fixture()
def form_pdf(tmp_path):
    """2행 3열, 첫 행의 1~2열이 병합된 표를 벡터 선으로 그린 디지털 PDF.

    ┌─────────────┬──────┐
    │  HEAD       │ R1C3 │
    ├──────┬──────┼──────┤
    │ A1   │ B1   │ C1   │
    └──────┴──────┴──────┘
    """
    rl = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "form.pdf"
    c = rl.Canvas(str(path), pagesize=(595, 842))
    xs, y_top, y_mid, y_bot = [100, 220, 320, 420], 700, 660, 620
    for y in (y_top, y_mid, y_bot):                # 가로선 3
        c.line(xs[0], y, xs[-1], y)
    for x in (xs[0], xs[2], xs[3]):                # 세로선 — 전체 높이
        c.line(x, y_bot, x, y_top)
    c.line(xs[1], y_bot, xs[1], y_mid)             # 병합: 위 칸엔 경계선 없음
    c.drawString(xs[0] + 8, y_mid + 15, "HEAD")
    c.drawString(xs[2] + 8, y_mid + 15, "R1C3")
    c.drawString(xs[0] + 8, y_bot + 15, "A1")
    c.drawString(xs[1] + 8, y_bot + 15, "B1")
    c.drawString(xs[2] + 8, y_bot + 15, "C1")
    c.save()
    return path


def test_grid_and_merge(form_pdf):
    tables = extract_tables(form_pdf)
    assert len(tables) == 1
    t = tables[0]
    assert (t.n_rows, t.n_cols) == (2, 3)

    merged = {(c.row, c.col): c for c in t.merged_cells}
    assert (0, 0) in merged
    assert merged[(0, 0)].col_span == 2 and merged[(0, 0)].row_span == 1


def test_cell_text_from_char_layer(form_pdf):
    t = extract_tables(form_pdf)[0]
    text_at = {(c.row, c.col): c.text for c in t.cells}
    assert text_at[(0, 0)] == "HEAD"
    assert text_at[(0, 2)] == "R1C3"
    assert text_at[(1, 0)] == "A1"
    assert text_at[(1, 1)] == "B1"
    assert text_at[(1, 2)] == "C1"


def test_column_widths_match_ruling(form_pdf):
    t = extract_tables(form_pdf)[0]
    # 실제 괘선 간격 120:100:100 → 비율 0.375:0.3125:0.3125
    assert len(t.col_w) == 3
    assert abs(t.col_w[0] - 120 / 320) < 0.01
    assert abs(sum(t.col_w) - 1.0) < 0.01


def test_page_without_lines_yields_nothing(tmp_path):
    rl = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "plain.pdf"
    c = rl.Canvas(str(path), pagesize=(595, 842))
    c.drawString(100, 700, "no table here")
    c.save()
    assert extract_tables(path) == []
