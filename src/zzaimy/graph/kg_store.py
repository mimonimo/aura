"""온톨로지 v3 저장 — 노드·관계를 DB 에 둔다(ADR-0048 5항: 규모 때문에 요청마다 다시 만들지 않는다).

노드 id 는 사람이 읽는 고정 값(program:…, year:…, doc:…, section:…)이고, 관계마다 기준(basis)과 근거(evidence)를 단다.
기준: 구조(목차·소속) · 분류(사업 분류의 근거) · 식별자 일치(같은 제목·지표·과제) · 추출(모델, 근거 문장 필수) · 유사도(추정).
근거를 댈 수 없는 관계는 만들지 않는다(v1 원칙).
"""

from __future__ import annotations

import json
from datetime import datetime

BASES = ("구조", "분류", "식별자 일치", "추출", "유사도")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS kg_nodes (id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL,"
    " props TEXT NOT NULL DEFAULT '{}', doc_id INTEGER, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS kg_edges (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, basis TEXT NOT NULL,"
    " evidence TEXT NOT NULL DEFAULT '[]', weight REAL NOT NULL DEFAULT 1.0, PRIMARY KEY (src, dst, kind))",
    # 들어오는 관계 조회(탐색 화면·연차→사업) — 기본 키는 src 쪽만 빠르다
    "CREATE INDEX IF NOT EXISTS kg_edges_dst ON kg_edges (dst)",
)


_READY: set[str] = set()


def ensure(db) -> None:
    """표·색인이 없을 때만 만든다(프로세스마다 한 번 확인).

    예전에는 화면 요청마다 CREATE … IF NOT EXISTS 를 보냈다. 그래프 갱신(157)이 표를 쓰는 동안 이 DDL 이 잠금을 기다리다
    시간 초과로 화면이 500 이 났다(2026-10-06 「온톨로지 인터널 에러」). 이미 있으면 DDL 을 아예 보내지 않는다."""
    key = str(getattr(db, "path", id(db)))
    if key in _READY:
        return
    with db._conn() as conn:
        have = set()
        try:      # PostgreSQL — 카탈로그만 읽는다(잠금 없음)
            for name in ("kg_nodes", "kg_edges", "kg_edges_dst"):
                if conn.execute("SELECT to_regclass(?)", (name,)).fetchone()[0]:
                    have.add(name)
        except Exception:
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name IN ('kg_nodes', 'kg_edges', 'kg_edges_dst')")}
        if not {"kg_nodes", "kg_edges", "kg_edges_dst"} <= have:
            for stmt in _SCHEMA:
                conn.execute(stmt)
    _READY.add(key)


def put_node(conn, node_id: str, type_: str, label: str, props: dict | None = None, doc_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO kg_nodes (id, type, label, props, doc_id, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET type = excluded.type, label = excluded.label, props = excluded.props,"
        " doc_id = excluded.doc_id, updated_at = excluded.updated_at",
        (node_id, type_, label[:300], json.dumps(props or {}, ensure_ascii=False), doc_id,
         datetime.now().isoformat(timespec="seconds")))


def put_edge(conn, src: str, dst: str, kind: str, basis: str, evidence: list[str] | None = None, weight: float = 1.0) -> None:
    if basis not in BASES:
        raise ValueError(f"관계 기준은 {BASES} 중 하나: {basis}")
    if not evidence:
        raise ValueError(f"근거 없는 관계는 만들지 않는다: {src} -{kind}-> {dst}")
    conn.execute(
        "INSERT INTO kg_edges (src, dst, kind, basis, evidence, weight) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(src, dst, kind) DO UPDATE SET basis = excluded.basis, evidence = excluded.evidence, weight = excluded.weight",
        (src, dst, kind, basis, json.dumps([str(e)[:240] for e in evidence[:5]], ensure_ascii=False), float(weight)))


def put_nodes(conn, nodes: list[tuple]) -> None:
    """put_node 를 묶어서(executemany) — 노드 수십만 개를 한 줄씩 보내면 DB 왕복이 재구축 시간을 먹는다."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for n in nodes:
        node_id, type_, label = n[0], n[1], n[2]
        props = n[3] if len(n) > 3 else None
        doc_id = n[4] if len(n) > 4 else None
        rows.append((node_id, type_, label[:300], json.dumps(props or {}, ensure_ascii=False), doc_id, now))
    for i in range(0, len(rows), 2000):
        conn.executemany(
            "INSERT INTO kg_nodes (id, type, label, props, doc_id, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET type = excluded.type, label = excluded.label, props = excluded.props,"
            " doc_id = excluded.doc_id, updated_at = excluded.updated_at", rows[i:i + 2000])


def put_edges(conn, edges: list[tuple]) -> None:
    """put_edge 를 묶어서 — 기준·근거 검사는 그대로(근거 없는 관계·모르는 기준은 거절)."""
    rows = []
    for e in edges:
        src, dst, kind, basis = e[0], e[1], e[2], e[3]
        evidence = e[4] if len(e) > 4 else None
        weight = e[5] if len(e) > 5 else 1.0
        if basis not in BASES:
            raise ValueError(f"관계 기준은 {BASES} 중 하나: {basis}")
        if not evidence:
            raise ValueError(f"근거 없는 관계는 만들지 않는다: {src} -{kind}-> {dst}")
        rows.append((src, dst, kind, basis, json.dumps([str(x)[:240] for x in evidence[:5]], ensure_ascii=False), float(weight)))
    for i in range(0, len(rows), 2000):
        conn.executemany(
            "INSERT INTO kg_edges (src, dst, kind, basis, evidence, weight) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(src, dst, kind) DO UPDATE SET basis = excluded.basis, evidence = excluded.evidence, weight = excluded.weight",
            rows[i:i + 2000])


def clear_doc(conn, doc_node: str) -> None:
    """한 문서의 절 노드와 그 관계를 지운다(다시 만들 때)."""
    like = doc_node + ":%"
    conn.execute("DELETE FROM kg_edges WHERE src LIKE ? OR dst LIKE ?", (like, like))
    conn.execute("DELETE FROM kg_nodes WHERE id LIKE ?", (like,))


def nodes(db, type_: str | None = None) -> list[dict]:
    with db._conn() as conn:
        q = "SELECT * FROM kg_nodes" + (" WHERE type = ?" if type_ else "") + " ORDER BY id"
        rows = conn.execute(q, (type_,) if type_ else ()).fetchall()
    return [{**dict(r), "props": json.loads(dict(r)["props"] or "{}")} for r in rows]


def edges(db, kind: str | None = None) -> list[dict]:
    with db._conn() as conn:
        q = "SELECT * FROM kg_edges" + (" WHERE kind = ?" if kind else "") + " ORDER BY src, dst"
        rows = conn.execute(q, (kind,) if kind else ()).fetchall()
    return [{**dict(r), "evidence": json.loads(dict(r)["evidence"] or "[]")} for r in rows]
