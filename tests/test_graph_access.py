"""문서 그래프(build_graph)는 /doc/{id} 와 같은 열람 규칙으로 문서를 거른다. 화면은 /graph/explore(test_kg_explore)."""
import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app
from zzaimy.graph import build, entities


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / "db.sqlite", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), password="test-password")
    db = app.state.db
    ids = {}
    for name, level, owner, dept in (
        ("public", "public", "other", "공통"),
        ("mine", "owner", "zzaimy", "공통"),
        ("private", "owner", "other", "공통"),
        ("department", "dept", "other", "학생처"),
    ):
        did = db.add_document(name + ".txt", "unused", doc_type="regulation")
        with db._conn() as conn:
            conn.execute("UPDATE documents SET access_level=?,owner=?,dept=? WHERE id=?",
                         (level, owner, dept, did))
        db.append_doc_chunks(did, [{"kind": "text", "content": "AUDIT evidence " + name}])
        ids[name] = did
    monkeypatch.setattr(build, "_add_project_relations", lambda *args: None)
    client = TestClient(app)
    client.auth = ("zzaimy", "test-password")
    return client, db, ids


STAFF = {"dept": None, "user": "zzaimy", "role": "staff"}


def _graph(db, dept="", scope=STAFF):
    return build.build_graph(db, dept=dept or None, scope=scope)


def test_staff_keeps_public_and_own_documents_but_cannot_expand_scope(setup):
    client, db, ids = setup
    for query in ("", "학생처"):
        graph = _graph(db, query)
        shown = {n["doc_id"] for n in graph["nodes"] if n["doc_id"] is not None}
        assert ids["private"] not in shown and ids["department"] not in shown
        if not query:
            assert shown == {ids["public"], ids["mine"]}
        node_ids = {n["id"] for n in graph["nodes"]}
        assert all(e["s"] in node_ids and e["t"] in node_ids for e in graph["edges"])


def test_admin_keeps_full_graph(setup):
    client, db, ids = setup
    shown = {n["doc_id"] for n in _graph(db, scope={"dept": None, "user": "zzdev", "role": "dev"})["nodes"]}
    assert set(ids.values()) <= shown


def test_hidden_documents_never_enter_entity_analysis(setup, monkeypatch):
    client, db, ids = setup
    original = entities.corpus_profile
    seen = []

    def profile(db, docs=None):
        seen.extend(d["id"] for d in docs)
        return original(db, docs=docs)

    monkeypatch.setattr(entities, "corpus_profile", profile)
    _graph(db)
    assert set(seen) == {ids["public"], ids["mine"]}


def test_private_project_and_its_edges_are_hidden(setup):
    client, db, ids = setup
    mine = db.create_project("grant", "My project", owner="zzaimy")
    other = db.create_project("grant", "Private project", owner="other")
    db.set_project_criteria(mine, [ids["public"], ids["private"]])
    db.set_project_criteria(other, [ids["public"]])
    with db._conn() as conn:
        conn.execute("UPDATE documents SET project_id=?,doc_type='auto' WHERE id=?", (other, ids["mine"]))
    graph = _graph(db)
    node_ids = {n["id"] for n in graph["nodes"]}
    assert f"p{mine}" in node_ids and f"p{other}" not in node_ids
    assert all(e["s"] in node_ids and e["t"] in node_ids for e in graph["edges"])
    assert any(e["s"] == f"p{mine}" and e["t"] == f"d{ids['public']}" for e in graph["edges"])


def test_empty_citation_scope_does_not_mean_all_documents(setup):
    from zzaimy.app.regulations import RegulationChunk

    client, db, ids = setup
    db.add_regulation_chunks(ids["public"], "공개 규정", [RegulationChunk(heading="1", content="비공개 규정 참조")])
    db.add_regulation_chunks(ids["private"], "비공개 규정", [RegulationChunk(heading="1", content="공개 규정 참조")])
    edges = []
    add = lambda *args, **kwargs: edges.append(args)
    build._add_citation_edges(db, [], add)
    assert edges == []
    build._add_citation_edges(db, [db.get_document(ids["public"])], add)
    assert edges == []
    build._add_citation_edges(db, db.list_documents("regulation"), add)
    assert edges


def test_similarity_only_uses_visible_documents(setup, monkeypatch, tmp_path):
    import numpy as np
    from zzaimy.app import embed_search
    from zzaimy.app.regulations import RegulationChunk

    client, db, ids = setup
    for name, did in ids.items():
        db.add_regulation_chunks(did, name, [RegulationChunk(heading="1", content="동일한 합성 근거")])
    chunks = db.list_regulation_chunks()
    path = tmp_path / "synthetic-vectors.npz"
    np.savez(path, ids=np.array([c["id"] for c in chunks]), vectors=np.ones((len(chunks), 2)))
    monkeypatch.setattr(embed_search, "INDEX_PATH", path)
    edges = []
    allowed = [db.get_document(ids[name]) for name in ("public", "mine")]
    build._add_similarity_edges(db, allowed, lambda s, t, *args, **kwargs: edges.append((s, t)))
    assert edges
    assert all({s, t} == {f"d{ids['public']}", f"d{ids['mine']}"} for s, t in edges)
    edges.clear()
    build._add_similarity_edges(db, [], lambda *args, **kwargs: edges.append(args))
    assert edges == []
