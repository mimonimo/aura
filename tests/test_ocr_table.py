"""OCR 표 열 폭 인출 — 글자줄 x 분포 → 열 경계, 신뢰도 낮으면 균등 폭 폴백."""

from __future__ import annotations

from zzaimy.ingest.parsers.base import ParsedEntry, ParsedTable, TableCell
from zzaimy.ingest.parsers.mineru import fill_ocr_col_widths
from zzaimy.ingest.parsers.ocr_table import derive_col_widths


def _rows(spans_per_row, n):
    """같은 3열 배치를 n행 반복한 글자줄 목록."""
    out = []
    for _ in range(n):
        out.extend(spans_per_row)
    return out


def test_three_columns_from_gutters():
    # 열: [2,18] [32,58] [72,98] — 거터 중심 25, 65
    spans = _rows([(2, 18), (32, 58), (72, 98)], 4)
    w = derive_col_widths(0.0, 100.0, spans, 3)
    assert len(w) == 3
    assert abs(sum(w) - 1.0) < 1e-6
    assert abs(w[0] - 0.25) < 0.02
    assert abs(w[1] - 0.40) < 0.02
    assert abs(w[2] - 0.35) < 0.02


def test_uneven_columns_reflect_real_positions():
    # 좁은 첫 열 + 넓은 둘째 열
    spans = _rows([(2, 8), (20, 95)], 5)
    w = derive_col_widths(0.0, 100.0, spans, 2)
    assert len(w) == 2
    assert w[0] < w[1]  # 원본 비율 반영: 첫 열이 더 좁다


def test_fallback_when_too_few_lines():
    assert derive_col_widths(0.0, 100.0, [(2, 18), (32, 58)], 3) == ()


def test_fallback_when_columns_touch():
    # 글자가 열 경계까지 꽉 차 거터가 없다 → 지어내지 않고 폴백
    spans = _rows([(2, 98)], 5)
    assert derive_col_widths(0.0, 100.0, spans, 3) == ()


def test_fallback_single_column():
    assert derive_col_widths(0.0, 100.0, _rows([(2, 98)], 5), 1) == ()


def test_fill_sets_col_w_on_ocr_table():
    t = ParsedTable(
        page_no=1, n_rows=4, n_cols=3,
        cells=(TableCell(row=0, col=0, text="x"),),
        bbox=(0.0, 0.0, 100.0, 50.0),
    )
    entry = ParsedEntry(page_no=1, kind="table", ref=0, bbox=(0.0, 0.0, 100.0, 50.0))
    ocr_lines = []
    for y in (5.0, 15.0, 25.0, 35.0):
        for lx0, lx1 in ((2.0, 18.0), (32.0, 58.0), (72.0, 98.0)):
            ocr_lines.append(
                {"page_no": 1, "bbox": f"{lx0},{y},{lx1},{y + 6}"}
            )
    out = fill_ocr_col_widths([t], [entry], ocr_lines)
    assert len(out[0].col_w) == 3
    assert abs(sum(out[0].col_w) - 1.0) < 1e-6


def test_fill_keeps_existing_col_w():
    t = ParsedTable(
        page_no=1, n_rows=2, n_cols=2,
        cells=(TableCell(row=0, col=0, text="x"),),
        col_w=(0.3, 0.7), bbox=(0.0, 0.0, 100.0, 50.0),
    )
    entry = ParsedEntry(page_no=1, kind="table", ref=0, bbox=(0.0, 0.0, 100.0, 50.0))
    ocr_lines = [
        {"page_no": 1, "bbox": f"{a},{y},{b},{y + 6}"}
        for y in (5.0, 15.0, 25.0)
        for a, b in ((2.0, 40.0), (55.0, 98.0))
    ]
    out = fill_ocr_col_widths([t], [entry], ocr_lines)
    assert out[0].col_w == (0.3, 0.7)  # 원본 폭이 있으면 덮어쓰지 않는다
