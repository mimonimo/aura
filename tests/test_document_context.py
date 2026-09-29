import json

import pytest

from zzaimy.document_context import Block, Document, build_context, validate


def sample():
    return Document("project-a", "notice", "v1", "announcement", (
        Block("h", 0, "heading", "지원 대상", structure_verified=True),
        Block("body", 1, "text", "참여 요건을 충족한 기관", "h", ("exception",),
              "p1", True),
        Block("exception", 2, "text", "다음 조건에 해당하는 기관은 제외", "h",
              locator="p2", structure_verified=True),
        Block("table", 3, "table", "지표|단위|2026 목표\n이수율|%|18.3", "h",
              ("note",), "p3", True),
        Block("note", 4, "note", "목표값이며 달성 실적이 아님", "h", ("table",),
              "p4", True),
    ))


def context(doc, seed, **kw):
    return build_context((doc,), program_id=doc.program_id, seeds=(doc.ref(seed),),
                         allowed_refs=kw.pop("allowed_refs", frozenset(doc.ref(b.id) for b in doc.blocks)), **kw)


def test_cross_page_exception_and_parent_are_retained():
    result = context(sample(), "body")
    assert [r["id"] for r in result.records] == ["h", "body", "exception"]
    assert result.complete and not result.needs_review


def test_table_headers_units_and_note_are_atomic_and_link_cycle_terminates():
    result = context(sample(), "table")
    assert [r["id"] for r in result.records] == ["h", "table", "note"]
    assert result.records[1]["text"] == sample().blocks[3].text


def test_budget_never_truncates_table():
    result = context(sample(), "table", max_chars=10)
    assert result.records == () and not result.complete
    assert result.issues == ("budget_exceeded",)


def test_denied_dependencies_are_not_leaked_and_context_is_incomplete():
    doc = sample()
    result = context(doc, "body", allowed_refs=frozenset({doc.ref("body")}))
    assert [r["id"] for r in result.records] == ["body"]
    assert not result.complete and result.issues == ("evidence_unavailable",)


def test_programs_do_not_mix():
    doc = sample()
    result = build_context((doc,), program_id="different-business", seeds=(doc.ref("body"),),
                           allowed_refs=frozenset({doc.ref("body")}))
    assert not result.records and not result.complete


def test_serialization_and_identity_preserve_revision():
    doc = sample()
    loaded = Document.from_dict(json.loads(json.dumps(doc.to_dict())))
    assert loaded == doc
    assert doc.ref("body") != Document("project-a", "notice", "v2", "announcement", ()).ref("body")


@pytest.mark.parametrize("blocks", [
    (Block("a", 0, "text", "x", "missing"),),
    (Block("a", 0, "text", "x", requires=("missing",)),),
    (Block("a", 0, "heading", "x", "b"), Block("b", 1, "heading", "y", "a")),
    (Block("a", 0, "text", "x"), Block("a", 1, "text", "y")),
])
def test_invalid_structure_rejected(blocks):
    with pytest.raises(ValueError):
        validate(Document("p", "d", "v1", "plan", blocks))


def test_unverified_structure_not_promoted_to_approved():
    doc = Document("p", "d", "v1", "plan", (Block("x", 0, "text", "original"),))
    assert context(doc, "x").needs_review
