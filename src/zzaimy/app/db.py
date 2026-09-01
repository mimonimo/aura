"""플랫폼 저장 계층 — SQLite.

문서 접수 이력, 처리 상태, 모델 검토 의견, 담당자 의견을 담는다.
인풋 문서의 본문은 마스킹본만 저장한다. 기준(regulation) 문서는 판단 근거라
개인 문서가 아니므로 원문 그대로 저장한다 (2026-09-02 결정).
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
CREATE TABLE IF NOT EXISTS chat_sessions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER,
  role       TEXT NOT NULL,      -- user | assistant
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  sector     TEXT NOT NULL,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_criteria (
  project_id      INTEGER NOT NULL REFERENCES projects(id),
  criteria_doc_id INTEGER NOT NULL REFERENCES documents(id),
  PRIMARY KEY (project_id, criteria_doc_id)
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
        "ALTER TABLE documents ADD COLUMN related_criteria_id INTEGER",
        "ALTER TABLE documents ADD COLUMN receipt_no TEXT",
        "ALTER TABLE chat_messages ADD COLUMN session_id INTEGER",
        "ALTER TABLE documents ADD COLUMN project_id INTEGER",
        "ALTER TABLE projects ADD COLUMN instructions TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN memo TEXT NOT NULL DEFAULT ''",
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

    _TYPE_CODES = {
        "grant": "국고", "recruit": "채용", "admission": "입학",
        "auto": "행정", "regulation": "기준",
    }

    def add_document(
        self,
        filename: str,
        stored_path: str,
        doc_type: str = "auto",
        sector: str = "common",
        related_criteria_id: int | None = None,
        project_id: int | None = None,
    ) -> int:
        now = _now()
        year = now[:4]
        code = self._TYPE_CODES.get(doc_type, "문서")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE doc_type = ? AND created_at LIKE ?",
                (doc_type, f"{year}%"),
            ).fetchone()
            receipt_no = f"{year}-{code}-{int(row[0]) + 1:04d}"
            cur = conn.execute(
                "INSERT INTO documents (filename, stored_path, doc_type, sector,"
                " related_criteria_id, project_id, receipt_no, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (filename, stored_path, doc_type, sector, related_criteria_id,
                 project_id, receipt_no, now),
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

    def list_documents(
        self,
        doc_type: str | None = None,
        q: str | None = None,
        project_id: int | None = None,
    ) -> list[dict]:
        sql = (
            "SELECT d.*, p.name AS project_name FROM documents d"
            " LEFT JOIN projects p ON p.id = d.project_id"
        )
        cond: list[str] = []
        params: list[str | int] = []
        if doc_type:
            cond.append("d.doc_type = ?")
            params.append(doc_type)
        if q:
            cond.append("d.filename LIKE ?")
            params.append(f"%{q}%")
        if project_id:
            cond.append("d.project_id = ?")
            params.append(project_id)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY d.id DESC"
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

    def failed_documents(self, limit: int = 5) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE status = 'failed' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_chat_session(self, title: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO chat_sessions (title, created_at) VALUES (?, ?)",
                (title[:60], _now()),
            )
            return int(cur.lastrowid or 0)

    def list_chat_sessions(self, limit: int = 12) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_sessions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_chat(self, session_id: int, role: str, content: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, created_at)"
                " VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )

    def list_chats(self, session_id: int, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def create_project(self, sector: str, name: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO projects (sector, name, created_at) VALUES (?, ?, ?)",
                (sector, name[:80], _now()),
            )
            return int(cur.lastrowid or 0)

    def list_projects(self, sector: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id)"
                " AS n_docs FROM projects p WHERE p.sector = ? ORDER BY p.id DESC",
                (sector,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def all_settings(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def list_all_projects(self) -> list[dict]:
        """사이드바용 — 섹터 구분 없이 전체 프로젝트 (문서 수 포함)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id)"
                " AS n_docs FROM projects p ORDER BY p.id DESC LIMIT 20",
            ).fetchall()
            return [dict(r) for r in rows]

    def update_project_meta(
        self, project_id: int, instructions: str | None = None, memo: str | None = None
    ) -> None:
        with self._conn() as conn:
            if instructions is not None:
                conn.execute(
                    "UPDATE projects SET instructions = ? WHERE id = ?",
                    (instructions[:4000], project_id),
                )
            if memo is not None:
                conn.execute(
                    "UPDATE projects SET memo = ? WHERE id = ?", (memo[:4000], project_id)
                )

    def set_project_criteria(self, project_id: int, criteria_doc_ids: list[int]) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM project_criteria WHERE project_id = ?", (project_id,)
            )
            conn.executemany(
                "INSERT OR IGNORE INTO project_criteria (project_id, criteria_doc_id)"
                " VALUES (?, ?)",
                [(project_id, cid) for cid in criteria_doc_ids],
            )

    def get_project_criteria_ids(self, project_id: int) -> list[int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT criteria_doc_id FROM project_criteria WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            return [r["criteria_doc_id"] for r in rows]

    def rename_project(self, project_id: int, name: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE projects SET name = ? WHERE id = ?", (name[:80], project_id)
            )
            return cur.rowcount > 0

    def delete_project(self, project_id: int) -> bool:
        """프로젝트만 지운다 — 소속 문서는 연결 해제 후 그대로 남는다."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE documents SET project_id = NULL WHERE project_id = ?",
                (project_id,),
            )
            conn.execute(
                "DELETE FROM project_criteria WHERE project_id = ?", (project_id,)
            )
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    def get_project(self, project_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_document(self, doc_id: int) -> None:
        """문서와 파생물(검토 의견·규정 조각)을 함께 지운다. 저장 파일은 호출부에서."""
        with self._conn() as conn:
            conn.execute("DELETE FROM reviews WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM regulation_chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

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
