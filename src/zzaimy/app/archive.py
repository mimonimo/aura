"""원본 목록 장부 — DGX 원본 보관소의 모든 파일을 VM 에서 알 수 있게(ADR-0047: 원본 보관은 DGX, 색인·분석은 VM).

사용자 2026-10-02: "어떤 문서가 DGX 에 있는지도 다 알아야 하지만 해당 부분 분석한 자료라던지 이런건 VM 에도 다 있어야 사용을 할거아냐".
파일 내용은 담지 않는다 — 경로·크기·수정 시각·형식과 사업 분류(graph/programs, 파일 이름·경로 기준)·갈래·연차, 문서함에 들인 경우 문서 번호.
DGX 에서 scripts/165 가 목록을 만들고 VM 에서 이 모듈로 들인다.
"""

from __future__ import annotations

import json
from datetime import datetime

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS archive_files (rel TEXT PRIMARY KEY, size BIGINT NOT NULL DEFAULT 0, mtime BIGINT NOT NULL DEFAULT 0,"
    " ext TEXT NOT NULL DEFAULT '', area TEXT NOT NULL DEFAULT '', program TEXT NOT NULL DEFAULT '', program_name TEXT NOT NULL DEFAULT '',"
    " status TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '', year INTEGER, round INTEGER, dup_of TEXT NOT NULL DEFAULT '',"
    " doc_id INTEGER, analysis TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS archive_files_program ON archive_files (program)",
)
FIELDS = ("rel", "size", "mtime", "ext", "area", "program", "program_name", "status", "kind", "year", "round", "dup_of")


def ensure(db) -> None:
    with db._conn() as conn:
        for q in _SCHEMA:
            conn.execute(q)


def load(db, rows: list[dict], origins: dict[str, int] | None = None) -> dict:
    """목록을 들인다(전체 교체가 아니라 덮어쓰기 — 사라진 파일은 seen_at 이 오래된 채 남아 '없어짐'으로 보인다)."""
    ensure(db)
    origins = origins or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n = 0
    with db._conn() as conn:
        for r in rows:
            vals = [r.get(k) if r.get(k) is not None else ("" if k not in ("size", "mtime", "year", "round") else None) for k in FIELDS]
            vals[1] = int(r.get("size") or 0)
            vals[2] = int(r.get("mtime") or 0)
            doc_id = origins.get(r["rel"])
            conn.execute(
                "INSERT INTO archive_files (rel, size, mtime, ext, area, program, program_name, status, kind, year, round, dup_of, doc_id, seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(rel) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, ext = excluded.ext, area = excluded.area,"
                " program = excluded.program, program_name = excluded.program_name, status = excluded.status, kind = excluded.kind,"
                " year = excluded.year, round = excluded.round, dup_of = excluded.dup_of,"
                " doc_id = COALESCE(excluded.doc_id, archive_files.doc_id), seen_at = excluded.seen_at",
                (*vals, doc_id, now))
            n += 1
    return {"rows": n, "at": now}


def summary(db) -> list[dict]:
    """사업 × 갈래별 파일 수와 문서함에 들인 수."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT program, program_name, kind, COUNT(*), SUM(CASE WHEN doc_id IS NOT NULL THEN 1 ELSE 0 END), SUM(size)"
            " FROM archive_files GROUP BY program, program_name, kind ORDER BY COUNT(*) DESC").fetchall()
    return [{"program": r[0], "program_name": r[1], "kind": r[2], "files": int(r[3]), "in_store": int(r[4] or 0),
             "bytes": int(r[5] or 0)} for r in rows]


def find(db, program: str = "", kind: str = "", text: str = "", limit: int = 50) -> list[dict]:
    """원본 찾기 — 사업·갈래·경로 글로."""
    ensure(db)
    q = "SELECT rel, size, ext, program_name, kind, year, round, doc_id FROM archive_files WHERE 1=1"
    args: list = []
    if program:
        q += " AND program = ?"
        args.append(program)
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    if text:
        q += " AND rel LIKE ?"
        args.append(f"%{text}%")
    q += " ORDER BY rel LIMIT ?"
    args.append(limit)
    with db._conn() as conn:
        rows = conn.execute(q, args).fetchall()
    keys = ("rel", "size", "ext", "program_name", "kind", "year", "round", "doc_id")
    return [dict(zip(keys, r)) for r in rows]


def parse_jsonl(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
