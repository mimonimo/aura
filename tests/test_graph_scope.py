"""Explicit scope must never silently expand to another year or document kind."""
import pytest

from zzaimy.app.db import Database
from zzaimy.graph import kg_store
from zzaimy.graph.retrieve import retrieve


@pytest.fixture
def graph(tmp_path):
    db = Database(tmp_path / "scope.db")
    kg_store.ensure(db)
    with db._conn() as conn:
        kg_store.put_node(conn, "program:alpha", "program", "ALPHA 사업",
                          {"names": ["ALPHA 사업"], "acronyms": ["ALPHA"]})
        kg_store.put_node(conn, "year:alpha:y2025", "year", "2025년", {"year": 2025, "round": 10})
        kg_store.put_edge(conn, "program:alpha", "year:alpha:y2025", "contains", "분류", ["연도"])
        for did, parent in ((1, "year:alpha:y2025"), (2, "program:alpha")):
            kg_store.put_node(conn, f"doc:{did}", "doc", "사업계획서", {"kind": "plan"}, did)
            kg_store.put_node(conn, f"doc:{did}:sec:1", "section", "참여 기업 지원", {"chunks": []}, did)
            kg_store.put_edge(conn, parent, f"doc:{did}", "contains", "분류", ["분류"])
            kg_store.put_edge(conn, f"doc:{did}", f"doc:{did}:sec:1", "contains", "구조", ["목차"])
    return db


def test_known_year_returns_only_that_year(graph):
    result = retrieve(graph, "ALPHA 2025년 계획서 참여 기업 지원")
    assert [h.doc_id for h in result.hits] == [1]


@pytest.mark.parametrize("question", [
    "ALPHA 2026년 계획서 참여 기업 지원",
    "ALPHA 2차년도 계획서 참여 기업 지원",
    "ALPHA 2025년 실적보고서 참여 기업 지원",
    "ALPHA 평가 결과 참여 기업 지원",
])
def test_missing_explicit_scope_does_not_return_other_evidence(graph, question):
    assert retrieve(graph, question).hits == []


def test_unspecified_year_keeps_yearless_documents(graph):
    assert {h.doc_id for h in retrieve(graph, "ALPHA 계획서 참여 기업 지원").hits} == {1, 2}


def test_multi_digit_round_is_not_ignored(graph):
    result = retrieve(graph, "ALPHA 10차년도 계획서 참여 기업 지원")
    assert result.round == 10
    assert [h.doc_id for h in result.hits] == [1]


def test_year_and_round_are_both_required_when_explicit(graph):
    result = retrieve(graph, "ALPHA 2026년 10차년도 계획서 참여 기업 지원")
    assert result.year == 2026 and result.round == 10
    assert result.hits == []
