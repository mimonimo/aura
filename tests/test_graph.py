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
    """이름이 'ㅇㅇ'처럼 근거가 없으면 추정하지 않는다(실측: 무의미한 이름이 임의 문서와 엮였음)."""
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
