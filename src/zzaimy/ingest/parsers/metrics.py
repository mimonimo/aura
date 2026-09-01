"""bake-off 채점 지표 (W1-W2 TASK-04).

채점 기준을 코드로 만든다 — 재현 가능한 지표:
- 표: 개수 일치, 행·열 수 정확도, 헤더 인식 재현율, 셀 텍스트 재현율,
  병합 셀 보존 재현율 (판정 1순위)
- 텍스트: 페이지별 필수 문구 누락률, 페이지 순서 보존
정답(GroundTruth)은 합성 샘플 생성 시 함께 만들어지며, 실물 표본은
사람이 30~50p 대조해 정답 파일을 작성한다 (TASK-09).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from zzaimy.ingest.parsers.base import ParsedTable, ParseResult


@dataclass(frozen=True)
class GroundTruthTable:
    page_no: int
    n_rows: int
    n_cols: int
    header_texts: list[str]
    cell_texts: list[str]
    n_merged_cells: int


@dataclass(frozen=True)
class GroundTruth:
    n_pages: int
    # 페이지 번호 → 그 페이지에 반드시 있어야 하는 문구들
    page_texts: dict[int, list[str]]
    tables: list[GroundTruthTable] = field(default_factory=list)


@dataclass(frozen=True)
class BakeoffScore:
    parser: str
    elapsed_s: float
    table_count_match: bool
    row_col_accuracy: float  # 정답 표 대비 행·열 수 일치 비율 (0~1)
    header_recall: float
    cell_text_recall: float
    merged_cell_recall: float
    text_miss_rate: float  # 필수 문구 누락률 (0=완벽)
    page_order_preserved: bool


def _norm(text: str) -> str:
    """비교용 정규화 — CID 폰트 PDF는 공백이 NBSP(\\xa0)로 추출되는 등
    파서마다 공백 표현이 달라, 정규화 없이 비교하면 전부 빗나간다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def load_ground_truth(path: Path) -> GroundTruth:
    """합성 샘플 생성기가 함께 쓰는 정답 JSON을 읽는다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return GroundTruth(
        n_pages=data["n_pages"],
        page_texts={int(k): v for k, v in data["page_texts"].items()},
        tables=[GroundTruthTable(**t) for t in data["tables"]],
    )


def _match_table(truth: GroundTruthTable, candidates: list[ParsedTable]) -> ParsedTable | None:
    """정답 표와 같은 페이지의 파싱 표 중 셀 텍스트가 가장 많이 겹치는 것."""
    same_page = [t for t in candidates if t.page_no == truth.page_no]
    if not same_page:
        return None
    truth_set = {_norm(t) for t in truth.cell_texts if t}

    def overlap(t: ParsedTable) -> int:
        parsed = {_norm(c.text) for c in t.cells if c.text}
        return len(truth_set & parsed)

    best = max(same_page, key=overlap)
    return best if overlap(best) > 0 else None


def score(result: ParseResult, truth: GroundTruth) -> BakeoffScore:
    matched: list[tuple[GroundTruthTable, ParsedTable | None]] = [
        (gt, _match_table(gt, result.tables)) for gt in truth.tables
    ]

    def ratio(hits: int, total: int) -> float:
        return hits / total if total else 1.0

    rowcol_hits = sum(
        1 for gt, t in matched if t is not None and t.n_rows == gt.n_rows and t.n_cols == gt.n_cols
    )

    header_total = sum(len(gt.header_texts) for gt, _ in matched)
    header_hits = 0
    cell_total = sum(len(gt.cell_texts) for gt, _ in matched)
    cell_hits = 0
    merged_total = sum(gt.n_merged_cells for gt, _ in matched)
    merged_hits = 0
    for gt, t in matched:
        if t is None:
            continue
        headers = {_norm(c.text) for c in t.cells if c.is_header}
        all_texts = {_norm(c.text) for c in t.cells}
        header_hits += sum(1 for h in gt.header_texts if _norm(h) in headers)
        cell_hits += sum(1 for c in gt.cell_texts if _norm(c) in all_texts)
        merged_hits += min(len(t.merged_cells), gt.n_merged_cells)

    text_total = sum(len(v) for v in truth.page_texts.values())
    page_text = {p.page_no: _norm(p.text) for p in result.pages}
    text_hits = sum(
        1
        for page_no, snippets in truth.page_texts.items()
        for s in snippets
        if _norm(s) in page_text.get(page_no, "")
    )

    # 페이지 순서: 필수 문구가 정답과 같은 페이지에서 발견되는지로 판정
    order_ok = (text_hits == text_total) and len(result.pages) == truth.n_pages

    return BakeoffScore(
        parser=result.parser,
        elapsed_s=result.elapsed_s,
        table_count_match=(
            len([t for _, t in matched if t is not None]) == len(truth.tables)
            and len(result.tables) >= len(truth.tables)
        ),
        row_col_accuracy=ratio(rowcol_hits, len(truth.tables)),
        header_recall=ratio(header_hits, header_total),
        cell_text_recall=ratio(cell_hits, cell_total),
        merged_cell_recall=ratio(merged_hits, merged_total),
        text_miss_rate=1.0 - ratio(text_hits, text_total) if text_total else 0.0,
        page_order_preserved=order_ok,
    )
