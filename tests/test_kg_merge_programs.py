"""이름이 같은 사업 노드는 하나로 합친다 — 간선이 많은 쪽에 간선·이름을 옮긴다(11-18)."""
from pathlib import Path

from zzaimy.app.db import Database
from zzaimy.graph import kg_store


def test_merge_duplicate_programs(tmp_path: Path):
    db = Database(tmp_path / "p.db")
    kg_store.ensure(db)
    with db._conn() as c:
        kg_store.put_node(c, "program:rise", "program", "앵커(구 RISE)", {"names": ["RISE"]})
        kg_store.put_node(c, "program:앵커", "program", "앵커(구 RISE)", {"names": ["앵커 사업"]})
        kg_store.put_node(c, "year:rise:r1", "year", "1차년도")
        kg_store.put_node(c, "year:앵커:r1", "year", "1차년도")
        for i in range(3):
            kg_store.put_node(c, f"doc:{i}", "doc", f"d{i}", doc_id=i)
            kg_store.put_edge(c, "year:rise:r1", f"doc:{i}", "contains", "구조", ["시험"])
        kg_store.put_edge(c, "program:rise", "year:rise:r1", "contains", "구조", ["시험"])
        kg_store.put_edge(c, "program:rise", "doc:0", "contains", "구조", ["시험"])
        kg_store.put_edge(c, "program:앵커", "year:앵커:r1", "contains", "구조", ["시험"])
    assert kg_store.merge_duplicate_programs(db) == [("program:앵커", "program:rise")]
    with db._conn() as c:
        assert [r[0] for r in c.execute("SELECT id FROM kg_nodes WHERE type = 'program'").fetchall()] == ["program:rise"]
        dst = {r[0] for r in c.execute("SELECT dst FROM kg_edges WHERE src = 'program:rise'").fetchall()}
        assert {"year:rise:r1", "year:앵커:r1", "doc:0"} <= dst
        import json
        assert set(json.loads(c.execute("SELECT props FROM kg_nodes WHERE id = 'program:rise'").fetchone()[0])["names"]) == {"RISE", "앵커 사업"}
