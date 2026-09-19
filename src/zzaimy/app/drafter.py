"""공고 → 계획서 초안 생성 (대시보드 통합판).

수직 슬라이스(scripts/90_slice_demo.py)와 같은 경로를 대시보드 버튼으로 노출한다.
공고 문서의 마스킹 본문에서 스키마를 추출하고, 섹션별로 근거 인출 → 생성 →
수치 검증을 돈 뒤 배점 커버리지와 함께 저장한다. 검색은 아직 스텁(P3 교체).
"""

from __future__ import annotations

import logging

from zzaimy.app.db import Database

log = logging.getLogger(__name__)


def _find_relevant(db: Database, query: str, top_k: int = 4) -> list[dict]:
    from zzaimy.app.regulations import find_relevant

    return find_relevant(db, query, top_k=top_k)


# 초안 재료가 되는 조각 종류 — 문단, 표, 그림 캡션·그림 속 글자
_MATERIAL_KINDS = ("text", "table", "image_text")


def material_text(c: dict) -> str:
    """조각 → 초안 재료 텍스트.

    표는 셀 JSON이 아니라 캡션+행 평문(render.table_text)으로 준다 — 모델이 표를
    읽을 수 있고, 표 속 수치가 수치 검증기의 근거 허용목록에 그대로 들어간다.
    """
    if c["kind"] == "table":
        from zzaimy.app.render import table_text

        return table_text(c["content"])[:900]
    return c["content"][:600]


_MATERIALS_PER_SECTION = 6


def build_section_evidence(
    db, section_query: str, materials: list, *, embed_fn=None, material_vecs=None,
) -> list:
    """섹션 근거 = 접수 자료 조각(섹션과 의미가 가까운 순) + 실검색 규정 조각.

    접수 자료는 문서 앞 6조각을 모든 섹션에 똑같이 넣던 방식을 버리고, 섹션
    질의와 각 조각의 학습 임베딩 코사인으로 섹션마다 골라 넣는다(예산 섹션엔
    예산 조각, 추진체계엔 체계 조각). material_vecs는 초안 1건당 한 번만
    계산해 넘긴다. 임베딩이 없으면 기존 순서 그대로(하위 호환).
    수치 검증기가 이 근거 텍스트를 허용 목록으로 쓰므로, 여기 들어온 것만이
    초안 수치의 출처가 될 수 있다.
    """
    from zzaimy.retrieve.stub import Evidence

    out = list(materials)
    if len(out) > _MATERIALS_PER_SECTION and embed_fn is not None and material_vecs is not None:
        try:
            qv = embed_fn([section_query])
            if qv is not None:
                sims = material_vecs @ qv[0]
                order = sims.argsort()[::-1]
                out = [out[i] for i in order]
        except Exception:
            log.warning("재료 의미 선별 실패 — 문서 순서 사용", exc_info=True)
    out = out[:_MATERIALS_PER_SECTION]
    try:
        hits = _find_relevant(db, section_query, top_k=4)
    except Exception:
        log.warning("규정 근거 검색 실패 — 자료 조각만 사용", exc_info=True)
        hits = []
    for h in hits:
        out.append(Evidence(
            text=f"{h.get('heading', '')} {h.get('content', '')}"[:600].strip(),
            source_doc=h.get("reg_title") or "규정",
            source_page=0,
        ))
    return out


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
        from zzaimy.retrieve.stub import Evidence
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

            # 담당자가 요구한 작성 항목(한 줄에 하나)이 있으면 공고 목차보다 우선
            spec = (doc.get("draft_spec") or "").strip()
            if spec:
                from zzaimy.generate.schema import SectionSpec

                names = [
                    ln.strip(" -•·\t") for ln in spec.splitlines() if ln.strip()
                ]
                if names:
                    schema.sections = [SectionSpec(name=n) for n in names[:12]]

            # 인풋 서류(신청서 등)가 공고와 다른 문서면 그 내용을 작성 재료로 쓴다
            materials: list[Evidence] = []
            if ref_name != doc["filename"]:
                # 구조 조각(문단·표·그림 글자)이 있으면 그것을 재료로 — 표 내용까지 근거가 된다
                chunks = [
                    (c, material_text(c)) for c in db.list_doc_chunks(doc_id)
                    if c["kind"] in _MATERIAL_KINDS
                ]
                chunks = [(c, t) for c, t in chunks if len(t) > 60]
                # 후보 풀은 넉넉히 — 섹션마다 의미로 골라 쓰므로 앞부분만 자르지 않는다
                if chunks:
                    materials = [
                        Evidence(
                            text=t, source_doc=doc["filename"],
                            source_page=c.get("page_no") or i + 1,
                        )
                        for i, (c, t) in enumerate(chunks[:40])
                    ]
                else:
                    paras = [
                        p.strip() for p in doc["masked_text"].split("\n\n")
                        if len(p.strip()) > 60
                    ]
                    materials = [
                        Evidence(text=p[:600], source_doc=doc["filename"], source_page=i + 1)
                        for i, p in enumerate(paras[:30])
                    ]

            # 재료 임베딩은 초안 1건당 한 번 — 섹션별 선별과 커버리지 의미 판정에 재사용
            from zzaimy.app.embed_search import embed_texts

            material_vecs = embed_texts([m.text for m in materials]) if materials else None

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

            parts: list[str] = [f"# {schema.title} — 계획서 초안 (70% 초안, 담당자 검토 전제)"]
            full_text = ""
            all_evidence: list[str] = []
            for section in schema.sections:
                evidence = build_section_evidence(
                    db, f"{section.name} {section.requirements}".strip(),
                    materials, embed_fn=embed_texts, material_vecs=material_vecs,
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
            from zzaimy.verify.budget import audit_budget_lines

            budget_issues = audit_budget_lines(full_text)
            coverage = check_coverage(
                [
                    {"name": c.name, "points": c.points, "keywords": c.keywords or [c.name]}
                    for c in schema.criteria
                ],
                full_text,
                embed_fn=embed_texts,   # 키워드 없어도 의미로 반영 여부 판정
            )
            summary = (
                f"배점 커버리지 {coverage.covered_points}/{coverage.total_points}점"
                + (f" · 누락: {', '.join(m.name for m in coverage.missing)}"
                   if coverage.missing else " · 누락 없음")
                + (" · 수치 검증 통과" if audit.ok
                   else " · 근거 없는 수치 {}건 — {}".format(
                       len(audit.violations),
                       " / ".join(audit.contexts[:3])
                       + (" 외" if len(audit.contexts) > 3 else ""),
                   ))
                + ("" if not budget_issues
                   else " · 예산 검산 불일치 {}건 — {}".format(
                       len(budget_issues), " / ".join(budget_issues[:2])
                   ))
            )
            new_draft = "\n\n".join(parts)
            # 재작성이면 이전 초안을 이력으로 보존한다 — 나중에 DPO 선호쌍
            # (이전=rejected, 현재=chosen)의 원천이 된다. 안 남기면 소급 불가.
            prev = (doc.get("draft") or "").strip()
            if prev and prev != new_draft.strip():
                try:
                    db.add_draft_history(doc_id, prev)
                except Exception:
                    log.warning("doc %d: 초안 이력 저장 실패(무시)", doc_id)
            db.update_document(doc_id, draft=new_draft, coverage=summary)
            log.info("doc %d: 초안 생성 완료 (%d 섹션)", doc_id, len(schema.sections))
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            log.exception("doc %d 초안 생성 실패", doc_id)
            db.update_document(doc_id, coverage=f"초안 생성 실패: {describe_llm_error(e)}")
