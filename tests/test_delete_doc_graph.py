"""문서를 지우면 그래프의 문서 노드와, 그 문서 때문에만 있던 연차·사업 노드도 사라진다."""
from zzaimy.app.db import Database
from zzaimy.graph import kg_store


def test_delete_document_drops_graph_nodes_left_alone(tmp_path):
    db = Database(tmp_path / "p.db")
    kg_store.ensure(db)
    gone = db.add_document("시험 운영계획.hwpx", "x", doc_type="grant")
    kept = db.add_document("다른 사업 계획.hwpx", "x", doc_type="grant")
    basis = sorted(kg_store.BASES)[0]
    with db._conn() as c:
        kg_store.put_node(c, f"doc:{gone}", "doc", "시험", doc_id=gone)
        kg_store.put_node(c, "year:t:y2026", "year", "시험사업 2026")
        kg_store.put_node(c, "program:t", "program", "시험사업")
        kg_store.put_edge(c, f"doc:{gone}", "year:t:y2026", "of_year", basis, ["시험"])
        kg_store.put_edge(c, "year:t:y2026", "program:t", "of_program", basis, ["시험"])
        kg_store.put_node(c, f"doc:{kept}", "doc", "다른", doc_id=kept)
        kg_store.put_node(c, "program:o", "program", "다른 사업")
        kg_store.put_edge(c, f"doc:{gone}", "program:o", "of_program", basis, ["시험"])
        kg_store.put_edge(c, f"doc:{kept}", "program:o", "of_program", basis, ["시험"])
    db.delete_document(gone)
    with db._conn() as c:
        ids = {r[0] for r in c.execute("SELECT id FROM kg_nodes").fetchall()}
        n_edges = c.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
    assert ids == {f"doc:{kept}", "program:o"}
    assert n_edges == 1
