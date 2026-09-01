"""플랫폼 저장 계층 — SQLite.

문서 접수 이력, 처리 상태, 모델 검토 의견, 담당자 의견을 담는다.
원문 텍스트는 마스킹본만 저장한다. 마스킹 전 텍스트는 DB에 넣지 않는다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  filename     TEXT NOT NULL,
  stored_path  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'received',
  -- received → processing → reviewed | failed
  series       TEXT,
  masked_text  TEXT,
  ai_review    TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL REFERENCES documents(id),
  opinion    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  role       TEXT NOT NULL,      -- user | assistant
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS regulation_chunks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL REFERENCES documents(id),
  reg_title  TEXT NOT NULL,
  heading    TEXT NOT NULL,
  content    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Database:
    # 스키마 추가분 — 기존 DB에 없으면 붙인다 (개발 단계 간이 마이그레이션)
    _MIGRATIONS = [
        "ALTER TABLE documents ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'auto'",
        "ALTER TABLE documents ADD COLUMN draft TEXT",
        "ALTER TABLE documents ADD COLUMN coverage TEXT",
        "ALTER TABLE documents ADD COLUMN decision TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE regulation_chunks ADD COLUMN sector TEXT NOT NULL DEFAULT 'common'",
        "ALTER TABLE documents ADD COLUMN sector TEXT NOT NULL DEFAULT 'common'",
    ]

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            for stmt in self._MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # 이미 있는 컬럼

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_document(
        self, filename: str, stored_path: str, doc_type: str = "auto", sector: str = "common"
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO documents (filename, stored_path, doc_type, sector, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (filename, stored_path, doc_type, sector, _now()),
            )
            return int(cur.lastrowid or 0)

    def update_document(self, doc_id: int, **fields: str | None) -> None:
        allowed = {
            "status", "series", "masked_text", "ai_review",
            "error", "draft", "coverage", "decision",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"허용되지 않은 필드: {unknown}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE documents SET {sets} WHERE id = ?", (*fields.values(), doc_id)
            )

    def get_document(self, doc_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def list_documents(self, doc_type: str | None = None, q: str | None = None) -> list[dict]:
        sql = "SELECT * FROM documents"
        cond, params = [], []
        if doc_type:
            cond.append("doc_type = ?")
            params.append(doc_type)
        if q:
            cond.append("filename LIKE ?")
            params.append(f"%{q}%")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def pending_documents(self, limit: int = 8) -> list[dict]:
        """판정 대기 — 검토는 끝났는데 담당자 판정이 없는 문서."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE status = 'reviewed' AND decision = 'pending'"
                " AND doc_type != 'regulation' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def chunks_for_docs(self, doc_ids: list[int]) -> list[dict]:
        if not doc_ids:
            return []
        marks = ",".join("?" for _ in doc_ids)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM regulation_chunks WHERE doc_id IN ({marks}) ORDER BY id",
                doc_ids,
            ).fetchall()
            return [dict(r) for r in rows]

    def add_chat(self, role: str, content: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chat_messages (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, _now()),
            )

    def list_chats(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def add_regulation_chunks(
        self, doc_id: int, reg_title: str, chunks, sector: str = "common"
    ) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM regulation_chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO regulation_chunks (doc_id, reg_title, heading, content, sector)"
                " VALUES (?, ?, ?, ?, ?)",
                [(doc_id, reg_title, c.heading, c.content, sector) for c in chunks],
            )

    def regulation_chunk_counts(self) -> dict[int, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT doc_id, COUNT(*) FROM regulation_chunks GROUP BY doc_id"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def list_regulation_chunks(self, sector: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if sector:
                rows = conn.execute(
                    "SELECT * FROM regulation_chunks WHERE sector IN (?, 'common') ORDER BY id",
                    (sector,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM regulation_chunks ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def add_review(self, doc_id: int, opinion: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO reviews (doc_id, opinion, created_at) VALUES (?, ?, ?)",
                (doc_id, opinion, _now()),
            )

    def get_reviews(self, doc_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE doc_id = ? ORDER BY id", (doc_id,)
            ).fetchall()
            return [dict(r) for r in rows]
