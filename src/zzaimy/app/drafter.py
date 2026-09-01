"""공고 → 계획서 초안 생성 (대시보드 통합판).

수직 슬라이스(scripts/90_slice_demo.py)와 같은 경로를 대시보드 버튼으로 노출한다.
공고 문서의 마스킹 본문에서 스키마를 추출하고, 섹션별로 근거 인출 → 생성 →
수치 검증을 돈 뒤 배점 커버리지와 함께 저장한다. 검색은 아직 스텁(P3 교체).
"""

from __future__ import annotations

import logging

from zzaimy.app.db import Database

log = logging.getLogger(__name__)


class SliceDrafter:
    def generate(self, db: Database, doc_id: int) -> None:
        from zzaimy.generate.client import VllmClient
        from zzaimy.retrieve.stub import StubRetriever
        from zzaimy.verify.coverage import check_coverage
        from zzaimy.verify.numbers import verify_numbers

        doc = db.get_document(doc_id)
        if doc is None or not doc.get("masked_text"):
            log.warning("doc %s: 초안 생성 불가 (본문 없음)", doc_id)
            return
        try:
            client = VllmClient()
            schema = client.extract_schema(doc["masked_text"])
            retriever = StubRetriever()
            criteria_text = ", ".join(f"{c.name}({c.points}점)" for c in schema.criteria)

            parts: list[str] = [f"# {schema.title} — 계획서 초안 (70%, 사람 검수 전제)"]
            full_text = ""
            all_evidence: list[str] = []
            for section in schema.sections:
                evidence = retriever.search(
                    section.requirements, user_access_levels={"internal"}
                )
                body = client.generate_section(
                    section.name, section.requirements, criteria_text,
                    direction="기관 강점 중심 차별화", evidence=evidence,
                )
                sources = "\n".join(
                    f"- 출처: {e.source_doc} p.{e.source_page}" for e in evidence
                )
                parts.append(f"## {section.name}\n{body}\n\n{sources}")
                full_text += "\n" + body
                all_evidence += [e.text for e in evidence] + [
                    f"{e.source_doc} {e.source_page}" for e in evidence
                ]

            audit = verify_numbers(full_text, all_evidence)
            coverage = check_coverage(
                [
                    {"name": c.name, "points": c.points, "keywords": c.keywords or [c.name]}
                    for c in schema.criteria
                ],
                full_text,
            )
            summary = (
                f"배점 커버리지 {coverage.covered_points}/{coverage.total_points}점"
                + (f" · 누락: {', '.join(m.name for m in coverage.missing)}"
                   if coverage.missing else " · 누락 없음")
                + (" · 수치 검증 통과" if audit.ok
                   else f" · 근거 없는 수치 {len(audit.violations)}건: {audit.violations}")
            )
            db.update_document(doc_id, draft="\n\n".join(parts), coverage=summary)
            log.info("doc %d: 초안 생성 완료 (%d 섹션)", doc_id, len(schema.sections))
        except Exception as e:
            log.exception("doc %d 초안 생성 실패", doc_id)
            db.update_document(doc_id, coverage=f"초안 생성 실패: {type(e).__name__}: {e}")
