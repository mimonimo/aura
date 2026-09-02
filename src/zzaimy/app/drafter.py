"""공고 → 계획서 초안 생성 (대시보드 통합판).

수직 슬라이스(scripts/90_slice_demo.py)와 같은 경로를 대시보드 버튼으로 노출한다.
공고 문서의 마스킹 본문에서 스키마를 추출하고, 섹션별로 근거 인출 → 생성 →
수치 검증을 돈 뒤 배점 커버리지와 함께 저장한다. 검색은 아직 스텁(P3 교체).
"""

from __future__ import annotations

import logging

from zzaimy.app.db import Database

log = logging.getLogger(__name__)


def _reference_text(db: Database, doc: dict) -> tuple[str, str]:
    """(공고·기준 텍스트, 출처명) — 문서 지정 기준 > 프로젝트 연결 기준 > 문서 자신."""
    ids: list[int] = []
    if doc.get("related_criteria_id"):
        ids = [int(doc["related_criteria_id"])]
    elif doc.get("project_id"):
        ids = db.get_project_criteria_ids(int(doc["project_id"]))
    for cid in ids:
        ref = db.get_document(cid)
        if ref and ref.get("masked_text"):
            return ref["masked_text"], ref["filename"]
    return doc["masked_text"], doc["filename"]


class SliceDrafter:
    def generate(self, db: Database, doc_id: int) -> None:
        from zzaimy.app.pipeline import _guidance_block
        from zzaimy.generate.client import VllmClient
        from zzaimy.retrieve.stub import Evidence, StubRetriever
        from zzaimy.verify.coverage import check_coverage
        from zzaimy.verify.numbers import verify_numbers

        doc = db.get_document(doc_id)
        if doc is None or not doc.get("masked_text"):
            log.warning("doc %s: 초안 생성 불가 (본문 없음)", doc_id)
            return
        try:
            client = VllmClient()
            # 공고문 참조: 연결된 공고·기준이 있으면 그 스키마로, 없으면 문서 자신
            ref_text, ref_name = _reference_text(db, doc)
            schema = client.extract_schema(ref_text[:12000])
            retriever = StubRetriever()

            # 인풋 서류(신청서 등)가 공고와 다른 문서면 그 내용을 작성 재료로 쓴다
            materials: list[Evidence] = []
            if ref_name != doc["filename"]:
                # 구조 조각(문단·표)이 있으면 그것을 재료로 — 표 내용까지 근거가 된다
                chunks = [
                    c for c in db.list_doc_chunks(doc_id)
                    if c["kind"] in ("text", "table") and len(c["content"]) > 60
                ]
                if chunks:
                    materials = [
                        Evidence(
                            text=c["content"][:600], source_doc=doc["filename"],
                            source_page=c.get("page_no") or i + 1,
                        )
                        for i, c in enumerate(chunks[:10])
                    ]
                else:
                    paras = [
                        p.strip() for p in doc["masked_text"].split("\n\n")
                        if len(p.strip()) > 60
                    ]
                    materials = [
                        Evidence(text=p[:600], source_doc=doc["filename"], source_page=i + 1)
                        for i, p in enumerate(paras[:8])
                    ]

            # 작성 방향: 기본 방향 + 담당자·프로젝트 지침 + 재작성 의견 반영
            project = (
                db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
            )
            direction = "기관 강점 중심 차별화" + _guidance_block(db, project)
            opinions = [r["opinion"] for r in db.get_reviews(doc_id)]
            if opinions:
                direction += "\n\n[담당자 재작성 요청 — 반드시 반영하라]\n" + "\n".join(
                    f"- {o}" for o in opinions
                )

            criteria_text = ", ".join(f"{c.name}({c.points}점)" for c in schema.criteria)

            parts: list[str] = [f"# {schema.title} — 계획서 초안 (70%, 사람 검수 전제)"]
            full_text = ""
            all_evidence: list[str] = []
            for section in schema.sections:
                evidence = materials or retriever.search(
                    section.requirements, user_access_levels={"internal"}
                )
                body = client.generate_section(
                    section.name, section.requirements, criteria_text,
                    direction=direction, evidence=evidence,
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
