"""수직 슬라이스 관통 데모 (W1-W2 TASK-08).

공고 PDF 1건 → 파싱 → 스키마 추출(guided_json) → 검색(스텁) → 섹션 1개 생성
→ 검증기 3종 → 출처 주석 문단 출력. 품질보다 경로 — 각 단계 실패는 명확히
로그에 남긴다. 발표 시연의 백업.

사용(Spark, vLLM 기동 상태):
    .venv/bin/python scripts/90_slice_demo.py data/demo/synthetic_announcement.pdf
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from zzaimy.generate.client import VllmClient
from zzaimy.generate.schema import AnnouncementSchema
from zzaimy.retrieve.stub import StubRetriever
from zzaimy.verify.budget import BudgetItem, compute_budget
from zzaimy.verify.coverage import check_coverage
from zzaimy.verify.numbers import verify_numbers

logging.basicConfig(level=logging.INFO, format="[슬라이스] %(message)s")
log = logging.getLogger(__name__)


def stage(name: str):
    log.info("=" * 8 + f" {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("announcement", type=Path)
    ap.add_argument("--direction", default="산학협력 강점을 중심으로 차별화")
    args = ap.parse_args()

    stage("1. 공고 파싱 (docling)")
    from zzaimy.ingest.parsers.docling import DoclingParser

    parsed = DoclingParser().parse(args.announcement)
    announcement_text = "\n".join(p.text for p in parsed.pages)
    for t in parsed.tables:
        announcement_text += "\n" + "\n".join(
            " | ".join(c.text for c in t.cells if c.row == r) for r in range(t.n_rows)
        )
    log.info("파싱 완료: %d페이지, 표 %d개, %d자", len(parsed.pages), len(parsed.tables),
             len(announcement_text))

    stage("2. 공고 스키마 추출 (vLLM guided_json)")
    client = VllmClient()
    schema: AnnouncementSchema = client.extract_schema(announcement_text)
    log.info("사업명: %s / 섹션 %d개 / 평가지표 %d개 / 예산상한 %s",
             schema.title, len(schema.sections), len(schema.criteria), schema.budget_limit_krw)

    stage("3. 근거 인출 (스텁 — P3에서 실검색으로 교체)")
    section = schema.sections[0]
    retriever = StubRetriever()
    evidence = retriever.search(section.requirements, user_access_levels={"internal"})
    log.info("근거 %d건 인출", len(evidence))

    stage("4. 섹션 1개 생성 (Qwen3-4B 베이스 — P5에서 ZZAIMY-Writer로 교체)")
    criteria_text = ", ".join(f"{c.name}({c.points}점)" for c in schema.criteria)
    draft = client.generate_section(
        section.name, section.requirements, criteria_text, args.direction, evidence
    )
    log.info("생성 완료: %d자", len(draft))

    stage("5. 검증기 3종")
    # 출처 표기(문서명·페이지)의 숫자도 근거가 있는 수치다 — 근거 집합에 포함
    evidence_texts = [e.text for e in evidence] + [
        f"{e.source_doc} {e.source_page}" for e in evidence
    ]
    audit = verify_numbers(draft, evidence_texts)
    log.info("수치 대조: %s (위반 %s)", "통과" if audit.ok else "위반", audit.violations or "없음")
    coverage = check_coverage(
        [{"name": c.name, "points": c.points, "keywords": c.keywords or [c.name]}
         for c in schema.criteria],
        draft,
    )
    log.info("배점 커버리지: %d/%d점, 누락 %s", coverage.covered_points, coverage.total_points,
             [m.name for m in coverage.missing] or "없음")
    budget = compute_budget(
        [BudgetItem(name="합성 장비", unit_price_krw=1_500_000, quantity=10)],
        limit_krw=schema.budget_limit_krw,
    )
    log.info("예산 검산: 합계 %s원, 상한 내 %s", f"{budget.total_krw:,}", budget.within_limit)

    stage("6. 출력 — 출처 주석 달린 초안")
    print("\n" + "=" * 60)
    print(f"[{schema.title}] {section.name} — 초안 (70%, 사람 검수 전제)")
    print("=" * 60)
    print(draft)
    print("-" * 60)
    print("출처:")
    for e in evidence:
        print(f"  · {e.source_doc} p.{e.source_page} — {e.text[:40]}…")
    if not audit.ok:
        print(f"⚠ 근거 없는 수치 발견: {audit.violations} — 검수 필요")
    if coverage.missing:
        print(f"⚠ 배점 누락 항목: {[m.name for m in coverage.missing]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
