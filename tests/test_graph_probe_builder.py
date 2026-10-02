import importlib.util
import json
import hashlib
from pathlib import Path
import sys

from zzaimy.app.db import Database
from zzaimy.graph import kg_store


def test_probes_are_scoped_unapproved_and_do_not_overwrite(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data/platform/platform.db"
    db_path.parent.mkdir(parents=True)
    db = Database(db_path)
    kg_store.ensure(db)
    for label in ("ALPHA", "BETA", "FAILED"):
        did = db.add_document(f"{label}.txt", "unused")
        db.update_document(did, status="failed" if label == "FAILED" else "reviewed")
        with db._conn() as conn:
            kg_store.put_node(conn, f"program:{label}", "program", f"{label} 사업")
            kg_store.put_node(conn, f"doc:{did}", "doc", f"{label} 계획서", {"kind": "plan"}, did)
            kg_store.put_node(conn, f"doc:{did}:sec:1", "section", "참여 목표", {"chunks": [0]}, did)
            kg_store.put_edge(conn, f"program:{label}", f"doc:{did}", "contains", "분류", ["사업"])
            kg_store.put_edge(conn, f"doc:{did}", f"doc:{did}:sec:1", "contains", "구조", ["제목"])
            conn.execute("INSERT INTO doc_chunks(doc_id,seq,content) VALUES(?,?,?)",
                         (did, 0, "참여 목표는 120명이며 계획서에 명시된 값이다. " * 5))
    spec = importlib.util.spec_from_file_location("probe_builder", Path(__file__).parents[1] / "scripts/build_graph_retrieval_probes.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(sys, "argv", ["builder", "--root", str(tmp_path), "--count", "10"])
    assert builder.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["count"] == 2 and not summary["eligible_for_training"]
    saved = Path(summary["path"]) / "candidates.jsonl"
    original = saved.read_text()
    snapshot = json.loads((saved.parent / "graph_snapshot.json").read_text())
    assert hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == summary["graph_snapshot_sha256"]
    rows = [json.loads(line) for line in original.splitlines()]
    assert {row["program_id"] for row in rows} == {"program:ALPHA", "program:BETA"}
    for row in rows:
        assert row["program_name"] in row["question"]
        assert row["answer"] is None and row["review"] is None
        assert row["year_node"] is None and "연차 미확인" in row["question"]
        assert len(row["evidence"]["source_hash"]) == 64
    assert builder.main() == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["path"] != summary["path"] and saved.read_text() == original
    assert repeated["graph_snapshot_sha256"] == summary["graph_snapshot_sha256"]
