"""지식 그래프 1단계(구조 그래프) — 온톨로지 v1 관계 생성 검증."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zzaimy.app.db import Database
from zzaimy.graph.build import build_graph


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "graph.db")


def _chunk(heading: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(heading=heading, content=content)


def test_nodes_and_refers_belongs_uses_edges(db):
    """접수→기준(refers), 문서→프로젝트(belongs), 프로젝트→기준(uses)."""
    reg_a = db.add_document("학칙.pdf", "/x/a", doc_type="regulation")
    reg_b = db.add_document("장학금 지급 지침.pdf", "/x/b", doc_type="regulation")
    proj = db.create_project("grant", "2026 학자금 지원")
    db.set_project_criteria(proj, [reg_b])
    intake = db.add_document(
        "신청서.pdf", "/x/c", doc_type="grant",
        related_criteria_id=reg_a, project_id=proj,
    )

    g = build_graph(db, include_similarity=False)
    ids = {n["id"] for n in g["nodes"]}
    assert {f"d{reg_a}", f"d{reg_b}", f"d{intake}", f"p{proj}"} <= ids

    kinds = {(e["s"], e["t"], e["kind"]) for e in g["edges"]}
    assert (f"d{intake}", f"d{reg_a}", "refers") in kinds
    assert (f"d{intake}", f"p{proj}", "belongs") in kinds
    assert (f"p{proj}", f"d{reg_b}", "uses") in kinds


def test_citation_edge_from_title_mention(db):
    """기준 조각 본문에 다른 기준 제목이 나오면 cites 간선."""
    reg_a = db.add_document("학칙.pdf", "/x/a", doc_type="regulation")
    reg_b = db.add_document("장학규정.pdf", "/x/b", doc_type="regulation")
    db.add_regulation_chunks(
        reg_a, "학칙",
        [_chunk("제10조", "장학금 지급은 장학규정 이 정하는 바에 따른다.")],
    )
    db.add_regulation_chunks(
        reg_b, "장학규정", [_chunk("제1조", "이 규정은 장학금 지급을 정한다.")]
    )

    g = build_graph(db, include_similarity=False)
    kinds = {(e["s"], e["t"], e["kind"]) for e in g["edges"]}
    assert (f"d{reg_a}", f"d{reg_b}", "cites") in kinds
    # 역방향은 없다 (장학규정 본문엔 "학칙" 언급 없음)
    assert (f"d{reg_b}", f"d{reg_a}", "cites") not in kinds


def test_edges_are_deduplicated(db):
    """지정 근거와 자동 제안이 같은 기준을 가리켜도 간선은 1개."""
    reg = db.add_document("학칙.pdf", "/x/a", doc_type="regulation")
    intake = db.add_document(
        "신청서.pdf", "/x/b", doc_type="grant", related_criteria_id=reg
    )
    db.update_document(intake, suggested_criteria=f'[{{"id": {reg}}}]')

    g = build_graph(db, include_similarity=False)
    refers = [e for e in g["edges"] if e["kind"] == "refers"]
    assert len(refers) == 1


def test_empty_db_yields_empty_graph(db):
    g = build_graph(db, include_similarity=False)
    assert g["nodes"] == [] and g["edges"] == []


def _fake_embed(texts):
    """결정론 가짜 임베딩 — 장학·학자금 주제면 [1,0], 아니면 [0,1] (학습 모델 자리)."""
    import numpy as np

    return np.array([[1.0, 0.0] if ("장학" in t or "학자금" in t) else [0.0, 1.0] for t in texts],
                    dtype="float32")


def test_project_relations_from_embeddings(db):
    """명시 연결이 없어도 프로젝트와 의미가 가까운 문서만 '추정 연관'으로 잇는다(돋보임 규칙)."""
    a = db.add_document("장학금 지급 지침.pdf", "/x/a", doc_type="regulation")
    b = db.add_document("학칙.pdf", "/x/b", doc_type="regulation")
    c = db.add_document("계약직원 임용 내규.pdf", "/x/c", doc_type="regulation")
    d = db.add_document("교직원 취업규칙.pdf", "/x/d", doc_type="regulation")
    proj = db.create_project("grant", "2026 학자금 지원 사업")

    # 새로 계산할 벡터가 많으면 요청을 막지 않고 배경에서 채운다 → 이번 빌드엔 없음
    g0 = build_graph(db, include_similarity=False, embed_fn=_fake_embed)
    assert not any(e["kind"] == "relates" for e in g0["edges"])
    import time as _t
    from pathlib import Path as _P
    for _ in range(50):                              # 배경 스레드가 캐시를 쓸 때까지
        _t.sleep(0.05)
        if (_P(db.path).parent / "doc_vectors.npz").exists():
            break
    g = build_graph(db, include_similarity=False, embed_fn=_fake_embed)
    rel = {(e["s"], e["t"]) for e in g["edges"] if e["kind"] == "relates"}
    assert rel == {(f"p{proj}", f"d{a}")}           # 주제가 같은 문서만, 나머지는 안 엮인다
    w = [e["w"] for e in g["edges"] if e["kind"] == "relates"][0]
    assert 0.99 <= w <= 1.0

    # 명시 연결(uses)이 있는 문서는 추정 연관으로 중복해서 잇지 않는다
    db.set_project_criteria(proj, [a])
    g2 = build_graph(db, include_similarity=False, embed_fn=_fake_embed)
    kinds = {(e["s"], e["t"], e["kind"]) for e in g2["edges"]}
    assert (f"p{proj}", f"d{a}", "uses") in kinds
    assert not any(k == "relates" for _, _, k in kinds)


def test_project_relations_need_a_descriptive_project(db):
    """이름이 'ㅇㅇ'처럼 근거가 없으면 추정하지 않는다(무의미한 이름은 임의 문서와 엮이기 쉽다)."""
    db.add_document("장학금 지급 지침.pdf", "/x/a", doc_type="regulation")
    db.add_document("학칙.pdf", "/x/b", doc_type="regulation")
    db.add_document("취업규칙.pdf", "/x/c", doc_type="regulation")
    db.create_project("grant", "ㅇㅇ")
    from pathlib import Path as _P
    g = build_graph(db, include_similarity=False, embed_fn=_fake_embed)
    import time as _t
    for _ in range(50):
        _t.sleep(0.05)
        if _P(db.path).parent.joinpath("doc_vectors.npz").exists():
            break
    g = build_graph(db, include_similarity=False, embed_fn=_fake_embed)
    assert not any(e["kind"] == "relates" for e in g["edges"])


def test_project_relations_skipped_without_model(db):
    db.add_document("장학금 지급 지침.pdf", "/x/a", doc_type="regulation")
    db.create_project("grant", "2026 학자금 지원 사업")
    g = build_graph(db, include_similarity=False, embed_fn=lambda texts: None)
    assert not any(e["kind"] == "relates" for e in g["edges"])


def test_same_title_documents_do_not_cite_each_other(tmp_path):
    """판이 다른 같은 제목의 매뉴얼 — 제 제목이 제 본문에 있을 뿐, 서로 인용이 아니다."""
    from zzaimy.app.db import Database
    from zzaimy.app.regulations import RegulationChunk
    from zzaimy.graph.build import _add_citation_edges

    db = Database(tmp_path / "t.db")
    edges = []
    for name in ("가구원 정보제공 동의 절차", "가구원 정보제공 동의 절차 · 웰로 앱"):
        d = db.add_document(filename=name, stored_path="", doc_type="regulation")
        db.add_regulation_chunks(d, name, [RegulationChunk(heading="1", content="가구원 정보제공 동의 절차 안내 본문입니다. 자세한 절차는 아래와 같습니다.")])
    other = db.add_document(filename="학자금 지원 규정", stored_path="", doc_type="regulation")
    db.add_regulation_chunks(other, "학자금 지원 규정", [RegulationChunk(heading="1", content="가구원 정보제공 동의 절차 를 따른다.")])
    _add_citation_edges(db, db.list_documents("regulation"), lambda s, t, k, **kw: edges.append((s, t)))
    assert all(not (s.startswith("d") and t.startswith("d") and s != f"d{other}") for s, t in edges)
    assert (f"d{other}", "d1") in edges


def test_suggested_criteria_edge_needs_project_or_program_evidence(db):
    """자동 추천 기준은 같은 프로젝트·같은 사업 근거가 있을 때만 refers 간선 — 유사도만으로 다른 사업을 잇지 않는다."""
    import json

    same = db.add_document("가상 사업 운영 지침.pdf", "/x/s", doc_type="regulation")
    other = db.add_document("다른 사업 기본계획.pdf", "/x/o", doc_type="regulation")
    intake = db.add_document("가상 사업 신청서.pdf", "/x/i", doc_type="grant")
    db.set_doc_identity(intake, {"program": "가상 바다 정화 지원 사업"})
    db.set_doc_identity(same, {"program": "가상 바다정화 지원사업 (2027)"})
    db.set_doc_identity(other, {"program": "다른 산간 교통 개선 사업"})
    db.update_document(intake, suggested_criteria=json.dumps(
        [{"id": other, "title": "다른"}, {"id": same, "title": "같은"}], ensure_ascii=False))

    g = build_graph(db, include_similarity=False)
    refers = {(e["s"], e["t"]): e for e in g["edges"] if e["kind"] == "refers"}
    assert (f"d{intake}", f"d{other}") not in refers                 # 다른 사업 — 유사도만으로는 잇지 않는다
    edge = refers[(f"d{intake}", f"d{same}")]
    assert edge["w"] == 0.7 and "같은 사업" in edge["why"]

    # 같은 프로젝트 근거 — 사업 이름이 달라도 프로젝트가 그 기준을 쓰면 잇는다
    pid = db.create_project("grant", "가상 묶음")
    db.set_project_criteria(pid, [other])
    member = db.add_document("가상 묶음 계획서.pdf", "/x/m", doc_type="grant", project_id=pid)
    db.set_doc_identity(member, {"program": "가상 바다 정화 지원 사업"})
    db.update_document(member, suggested_criteria=json.dumps([{"id": other, "title": "다른"}]))
    g = build_graph(db, include_similarity=False)
    refers = {(e["s"], e["t"]): e for e in g["edges"] if e["kind"] == "refers"}
    assert "같은 프로젝트" in refers[(f"d{member}", f"d{other}")]["why"]
    assert (f"d{intake}", f"d{other}") not in refers
