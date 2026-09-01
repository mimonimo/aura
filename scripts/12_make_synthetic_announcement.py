"""합성 공고문 PDF 생성 (W1-W2 TASK-08).

수직 슬라이스 입력용. 내용은 전부 지어낸 것이며 실제 사업과 무관하다.

사용: python scripts/12_make_synthetic_announcement.py  # data/demo/에 생성
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "HYSMyeongJo-Medium"

BODY = [
    "2026년 합성전문대학 역량강화 지원사업 공고",
    "1. 사업 개요: 전문대학의 교육 역량 강화를 위한 가상의 지원사업이다. "
    "총 사업 예산 상한은 500,000,000원이다.",
    "2. 계획서 목차: 계획서는 다음 세 개 섹션으로 구성한다.",
    "  가. 사업 추진 필요성 — 기관 현황과 추진 배경을 서술",
    "  나. 추진 전략 — 산학협력과 취업 지원 전략을 제시",
    "  다. 기대 효과 — 정량 목표를 제시",
    "3. 평가지표는 아래 배점표를 따른다.",
]

CRITERIA_TABLE = [
    ["평가지표", "배점", "핵심 요건"],
    ["산학협력 실적", "30", "산학협력 협약·공동 프로그램"],
    ["취업 지원 체계", "40", "취업 프로그램 운영·수료율"],
    ["재정 집행 계획", "30", "예산 편성의 적정성"],
]


def main() -> None:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    body = ParagraphStyle("body", fontName=FONT, fontSize=11, leading=17)
    grid = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), FONT),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]
    )
    out = Path("data/demo")
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / "synthetic_announcement.pdf"
    flow = [Paragraph(t, body) for t in BODY]
    flow.insert(1, Spacer(1, 8))
    flow.append(Spacer(1, 10))
    flow.append(Table(CRITERIA_TABLE, style=grid))
    SimpleDocTemplate(str(pdf_path), pagesize=A4).build(flow)
    print(f"생성됨: {pdf_path}")


if __name__ == "__main__":
    main()
