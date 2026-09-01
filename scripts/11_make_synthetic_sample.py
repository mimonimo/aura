"""합성 표본 PDF + 정답 파일 생성 (W1-W2 TASK-04).

실물 표본이 도착하기 전 bake-off 하네스를 검증하기 위한 문서다.
내용은 전부 지어낸 합성 데이터이며 실제 사업·인물과 무관하다.

사용:
    python scripts/11_make_synthetic_sample.py            # data/demo/에 생성
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "HYSMyeongJo-Medium"  # reportlab 내장 한국어 CID 폰트 — 폰트 파일 불필요

P1_TEXT = "본 사업은 합성대학교의 교육 역량 강화를 위한 가상의 시범 사업이다."
P2_TEXT = "프로그램 운영 실적은 아래 표와 같이 병합 셀을 포함해 정리되었다."
P3_TEXT = "기대 효과로는 합성 지표의 전반적 개선이 예상된다."

SIMPLE_TABLE = [
    ["사업명", "연도", "참여인원"],
    ["합성역량강화사업", "2023", "120"],
    ["가상혁신지원사업", "2024", "95"],
]

MERGED_TABLE = [
    ["실적 구분", "실적 구분", "비고"],  # (0,0)-(0,1) 가로 병합
    ["프로그램 수", "14", "누적"],
    ["만족도", "4.2", "5점 만점"],
]


def build_pdf(pdf_path: Path) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    body = ParagraphStyle("body", fontName=FONT, fontSize=11, leading=16)
    grid = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), FONT),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]
    )
    merged_grid = TableStyle(grid.getCommands() + [("SPAN", (0, 0), (1, 0))])

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4)
    doc.build(
        [
            Paragraph(P1_TEXT, body),
            Spacer(1, 12),
            Table(SIMPLE_TABLE, style=grid),
            PageBreak(),
            Paragraph(P2_TEXT, body),
            Spacer(1, 12),
            Table(MERGED_TABLE, style=merged_grid),
            PageBreak(),
            Paragraph(P3_TEXT, body),
        ]
    )


def build_truth(truth_path: Path) -> None:
    truth = {
        "n_pages": 3,
        "page_texts": {"1": [P1_TEXT], "2": [P2_TEXT], "3": [P3_TEXT]},
        "tables": [
            {
                "page_no": 1,
                "n_rows": 3,
                "n_cols": 3,
                "header_texts": SIMPLE_TABLE[0],
                "cell_texts": [c for row in SIMPLE_TABLE for c in row],
                "n_merged_cells": 0,
            },
            {
                "page_no": 2,
                "n_rows": 3,
                "n_cols": 3,
                "header_texts": ["실적 구분", "비고"],
                "cell_texts": sorted({c for row in MERGED_TABLE for c in row}),
                "n_merged_cells": 1,
            },
        ],
    }
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    out_dir = Path("data/demo")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "synthetic_sample.pdf"
    build_pdf(pdf_path)
    build_truth(out_dir / "synthetic_sample.truth.json")
    print(f"생성됨: {pdf_path} (+ .truth.json)")


if __name__ == "__main__":
    main()
