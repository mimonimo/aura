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

    def add_document(self, filename: str, stored_path: str, doc_type: str = "auto") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO documents (filename, stored_path, doc_type, created_at)"
                " VALUES (?, ?, ?, ?)",
                (filename, stored_path, doc_type, _now()),
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

    def list_documents(self, doc_type: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if doc_type:
                rows = conn.execute(
                    "SELECT * FROM documents WHERE doc_type = ? ORDER BY id DESC", (doc_type,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
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
