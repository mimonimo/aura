#!/usr/bin/env python3
"""Build business-scoped self-retrieval probes. These are not gold or SFT data.

Read-only DB snapshot; output goes to a new, exclusive directory under data/eval.
No model calls, document mutations, indexing, publishing, or approval.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--count", type=int, default=30)
    args = ap.parse_args()
    if not 1 <= args.count <= 100:
        ap.error("count must be between 1 and 100")
    root = args.root.resolve()
    sys.path.insert(0, str(root / "src"))
    from zzaimy.app.database_backend import connect

    with closing(connect(root / "data/platform/platform.db", readonly=True)) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                     if getattr(conn, "dialect", "") == "postgres" else "BEGIN")
        nodes = {r["id"]: {**dict(r), "props": json.loads(r["props"] or "{}")}
                 for r in conn.execute("SELECT * FROM kg_nodes ORDER BY id").fetchall()}
        edges = [dict(r) for r in conn.execute(
            "SELECT src,dst FROM kg_edges WHERE kind='contains' ORDER BY src,dst").fetchall()]
        statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM documents").fetchall()}
        parents = {}
        for edge in edges:
            parents.setdefault(edge["dst"], []).append(edge["src"])

        def ancestry(node_id):
            path, seen = [], set()
            while node_id in nodes and node_id not in seen:
                seen.add(node_id)
                path.append(nodes[node_id])
                ps = parents.get(node_id, [])
                if len(ps) != 1:
                    break
                node_id = ps[0]
            return path

        groups = {}
        for node in nodes.values():
            if node["type"] != "section" or statuses.get(node["doc_id"]) != "reviewed":
                continue
            if not node["props"].get("chunks"):
                continue
            path = ancestry(node["id"])
            programs = [n for n in path if n["type"] == "program"]
            docs = [n for n in path if n["type"] == "doc"]
            if len(programs) != 1 or len(docs) != 1:
                continue
            groups.setdefault(programs[0]["id"], []).append((node, path, docs[0], programs[0]))
        for items in groups.values():
            items.sort(key=lambda item: hashlib.sha256(item[0]["id"].encode()).hexdigest())

        snapshot = hashlib.sha256(json.dumps(
            {"nodes": nodes, "contains": edges}, sort_keys=True, ensure_ascii=False
        ).encode()).hexdigest()
        rows, seen_docs = [], set()
        while len(rows) < args.count and any(groups.values()):
            for pid in sorted(groups):
                items = groups[pid]
                while items:
                    node, path, doc, program = items.pop(0)
                    if node["doc_id"] in seen_docs:
                        continue
                    seqs = node["props"]["chunks"]
                    marks = ",".join("?" for _ in seqs)
                    chunk = conn.execute(
                        f"SELECT id,seq,content,page_no FROM doc_chunks WHERE doc_id=? AND seq IN ({marks}) "
                        "AND length(trim(content)) >= 60 ORDER BY seq LIMIT 1",
                        [node["doc_id"], *seqs],
                    ).fetchone()
                    if chunk is None:
                        continue
                    years = [n for n in path if n["type"] == "year"]
                    year = years[0] if years else None
                    period = year["label"] if year else "연차 미확인"
                    source = chunk["content"]
                    rows.append({
                        "id": f"probe:{len(rows)+1}", "status": "candidate_unreviewed",
                        "evaluation_type": "title_based_self_retrieval", "eligible_for_training": False,
                        "program_id": pid, "program_name": program["label"],
                        "year_node": year["id"] if year else None, "document_kind": doc["props"].get("kind"),
                        "question": f"{program['label']}의 {period} 자료 중 「{doc['label']}」의 「{node['label']}」에 기록된 내용은 무엇인가요?",
                        "expected_nodes_candidate": [node["id"]], "doc_id": node["doc_id"],
                        "node_path": [n["label"] for n in reversed(path)],
                        "evidence": {"chunk_id": chunk["id"], "seq": chunk["seq"], "page_no": chunk["page_no"],
                                     "text": source, "source_hash": hashlib.sha256(source.encode()).hexdigest()},
                        "graph_snapshot_sha256": snapshot, "classification": doc["props"],
                        "answer": None, "review": None,
                    })
                    seen_docs.add(node["doc_id"])
                    break
                if len(rows) >= args.count:
                    break
        conn.rollback()
    if not rows:
        print(json.dumps({"status": "no_eligible_sections", "count": 0}))
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / "data/eval/graph-probes" / f"{stamp}-{uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    with (target / "candidates.jsonl").open("x", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    # Freeze the exact graph for repeatable evaluation while another worker rebuilds it.
    with (target / "graph_snapshot.json").open("x", encoding="utf-8") as out:
        json.dump({"nodes": nodes, "contains": edges}, out, ensure_ascii=False, sort_keys=True)
    counts = {}
    for row in rows:
        counts[row["program_id"]] = counts.get(row["program_id"], 0) + 1
    summary = {"status": "candidate_unreviewed", "count": len(rows), "programs": counts,
               "graph_snapshot_sha256": snapshot, "path": str(target), "eligible_for_training": False}
    with (target / "summary.json").open("x", encoding="utf-8") as out:
        json.dump(summary, out, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
