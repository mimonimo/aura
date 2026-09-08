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


def test_graph_json_route(tmp_path):
    from fastapi.testclient import TestClient

    # 경로 무관 임포트 — 'pytest'로 직접 돌려도(루트가 sys.path에 없어도) 동작
    from test_app import FakeDrafter, FakeProcessor
    from zzaimy.app.main import create_app

    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    client = TestClient(app)
    r = client.get("/graph.json")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body
