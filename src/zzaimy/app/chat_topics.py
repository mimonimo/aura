"""대화의 근거와 주제 — 답변마다 검색 근거를 남기고, 대화의 주제를 그 근거에서 정한다.

왜: 일반 대화는 첫 질문만 제목으로 남아 나중에 무슨 사업·규정 이야기였는지 찾기 어렵다.
모델에게 주제를 지어내게 하지 않고, 답변이 실제로 근거로 쓴 문서 중 가장 많이 쓰인
문서의 사업 이름(없으면 규정 이름)을 주제로 삼는다. 근거가 약한(하한 미달) 것은 세지 않는다.
근거는 예전엔 메모리에만 있어 앱을 다시 켜면 대화 옆 '연관 자료'도 비었다 — 이제 DB 에 남는다.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS chat_sources (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  batch      INTEGER NOT NULL,           -- 같은 답변의 근거 묶음(답변마다 1 증가)
  doc_id     INTEGER,
  title      TEXT NOT NULL,
  heading    TEXT NOT NULL DEFAULT '',
  snippet    TEXT NOT NULL DEFAULT '',
  origin     TEXT NOT NULL DEFAULT '',
  weak       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_sources_session ON chat_sources(session_id, batch);
"""


class ChatTopics:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, session_id: int, sources: list[dict]) -> None:
        """한 답변의 근거를 남긴다. 근거가 없으면 남기지 않는다."""
        if not sources:
            return
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            batch = conn.execute(
                "SELECT COALESCE(MAX(batch), 0) + 1 FROM chat_sources WHERE session_id = ?",
                (session_id,)).fetchone()[0]
            conn.executemany(
                "INSERT INTO chat_sources (session_id, batch, doc_id, title, heading, snippet,"
                " origin, weak, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                [(session_id, batch, s.get("doc_id"), (s.get("title") or "문서")[:200],
                  (s.get("heading") or "")[:200], (s.get("snippet") or "")[:300],
                  s.get("origin") or "", int(bool(s.get("weak"))), now) for s in sources])

    def latest(self, session_id: int) -> list[dict]:
        """가장 최근 답변의 근거 — 화면의 '연관 자료'."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT doc_id, title, heading, snippet, origin, weak FROM chat_sources"
                " WHERE session_id = ? AND batch = (SELECT MAX(batch) FROM chat_sources"
                " WHERE session_id = ?) ORDER BY id", (session_id, session_id)).fetchall()
        return [dict(r, weak=bool(r["weak"])) for r in rows]

    @staticmethod
    def match_clause(query: str) -> tuple[str, list]:
        """주제 검색 조건 — (SQL 조각, 인자). 기록 목록 질의에 OR 로 붙여 한 번에 거른다.

        주제 이름을 정하는 순서(사업 > 문서에서 찾은 이름 > 근거 제목)와 같은 값을 본다.
        약한 근거는 주제로 삼지 않으므로 여기서도 뺀다.
        """
        key = f"%{query.strip().lower()}%"
        sql = ("EXISTS (SELECT 1 FROM chat_sources cs LEFT JOIN documents d ON d.id = cs.doc_id"
               " WHERE cs.session_id = s.id AND cs.weak = 0 AND ("
               " lower(COALESCE(json_extract(d.identity, '$.program'), '')) LIKE ?"
               " OR lower(COALESCE(json_extract(d.identity, '$.title'), '')) LIKE ?"
               " OR lower(cs.title) LIKE ?))")
        return sql, [key, key, key]

    def topics(self, session_ids: list[int]) -> dict[int, str]:
        """대화별 주제 이름. 근거가 없는 대화는 빠진다."""
        if not session_ids:
            return {}
        marks = ",".join("?" * len(session_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT cs.session_id, cs.doc_id, cs.title, cs.weak, d.filename, d.identity,"
                f" d.stored_path, d.masked_text FROM chat_sources cs"
                f" LEFT JOIN documents d ON d.id = cs.doc_id"
                f" WHERE cs.session_id IN ({marks})", session_ids).fetchall()
        by_session: dict[int, Counter] = {}
        labels: dict[tuple, str] = {}
        for r in rows:
            # 하한을 못 넘은 약한 근거는 주제로 삼지 않는다 — "문서 접수 해줘" 같은 질문에
            # 우연히 걸린 문서가 주제가 되면 기록을 더 헷갈리게 한다(실측 2026-09-20)
            if r["weak"]:
                continue
            key = ("d", r["doc_id"]) if r["doc_id"] else ("t", r["title"])
            by_session.setdefault(r["session_id"], Counter())[key] += 1.0
            if key not in labels:
                labels[key] = _label(r)
        return {sid: labels[c.most_common(1)[0][0]] for sid, c in by_session.items() if c}


def _label(r: sqlite3.Row) -> str:
    """주제 이름 — 문서의 사업 이름 > 문서에서 찾은 정식 이름 > 근거 제목."""
    if r["doc_id"] and r["filename"] is not None:
        try:
            ident = json.loads(r["identity"] or "{}")
        except ValueError:
            ident = {}
        if ident.get("program"):
            return ident["program"]
        from zzaimy.app.doc_title import display_name

        name = display_name({"filename": r["filename"], "identity": r["identity"],
                             "stored_path": r["stored_path"], "masked_text": r["masked_text"]})
        # 날짜는 주제 이름에 필요 없다 — '… 규정 · 2022년 05월 26일' 에서 이름만
        name = name.split(" · ")[0]
        return name if name != r["filename"] else Path(name).stem
    return r["title"]
