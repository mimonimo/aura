"""개체 추출(온톨로지 v2) — 결정론 규칙과 그래프 승격 기준 검증."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zzaimy.app.db import Database
from zzaimy.graph.build import build_graph
from zzaimy.graph.entities import extract_doc_entities, extract_mentions


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "e.db")


def _chunk(heading: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(heading=heading, content=content)


def test_extract_year_org_program():
    text = (
        "2026년 대학혁신지원사업 계획. 한국연구재단과 교내 산학협력단이 "
        "협력하며, 국가장학금 및 지역혁신사업 연계를 포함한다. 2026년 접수."
    )
    got = extract_mentions(text)
    assert got[("2026년", "year")] == 2
    assert got[("대학혁신지원사업", "program")] == 1
    assert got[("한국연구재단", "org")] == 1
    assert got[("지역혁신사업", "program")] == 1


def test_generic_terms_are_not_entities():
    got = extract_mentions("이 지원사업 은 재단 이 주관한다. 장학금 지급.")
    assert not got, f"일반어가 개체로 잡힘: {dict(got)}"


def test_numbers_alone_are_not_years():
    got = extract_mentions("예산 2026백만원, 항목 1999건")
    assert ("2026년", "year") not in got


def test_entity_promotion_requires_two_docs(db):
    """한 문서에만 나오는 개체는 그래프 노드로 올리지 않는다."""
    a = db.add_document("a.pdf", "/x", doc_type="regulation")
    b = db.add_document("b.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(a, "a", [_chunk("1", "대학혁신지원사업 2026년 시행")])
    db.add_regulation_chunks(b, "b", [_chunk("1", "대학혁신지원사업 안내")])
    extract_doc_entities(db)

    g = db.graph_entities(min_docs=2)
    names = {e["name"] for e in g["entities"]}
    assert "대학혁신지원사업" in names   # 두 문서 연결
    assert "2026년" not in names         # 한 문서뿐

    graph = build_graph(db, include_similarity=False)
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "entity" in kinds
    mention_edges = [e for e in graph["edges"] if e["kind"] == "mentions"]
    assert len(mention_edges) == 2  # 두 문서 → 개체 1개


def test_reextraction_is_idempotent(db):
    a = db.add_document("a.pdf", "/x", doc_type="regulation")
    b = db.add_document("b.pdf", "/x", doc_type="regulation")
    for did in (a, b):
        db.add_regulation_chunks(did, "t", [_chunk("1", "한국연구재단 공고")])
    extract_doc_entities(db)
    extract_doc_entities(db)
    g = db.graph_entities(min_docs=2)
    assert len(g["entities"]) == 1
    assert len(g["links"]) == 2
