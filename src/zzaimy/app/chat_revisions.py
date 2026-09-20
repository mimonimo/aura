"""질문 수정과 이전 대화 보관. 원래 질문 ID는 유지하며 이후 대화는 이력에 보관한다."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, File, Form, HTTPException, Request, UploadFile


class ChatRevisions:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chat_message_context (
                    message_id INTEGER PRIMARY KEY, attachment TEXT,
                    criteria TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS chat_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL, created_at TEXT NOT NULL,
                    messages TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_revisions_session ON chat_revisions(session_id);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def remember(self, message_id: int, attachment: Path | None, criteria: list[int]):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chat_message_context VALUES (?, ?, ?)",
                (message_id, str(attachment) if attachment else None, json.dumps(criteria)),
            )

    def history(self, session_id: int):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_revisions WHERE session_id=? ORDER BY id DESC", (session_id,)
            ).fetchall()
        return [dict(row) | {"messages": json.loads(row["messages"])} for row in rows]

    def edit(self, session_id, message_id, owner, question, expected, tail_id,
             replacement: Path | None = None, filename: str = ""):
        question = question.strip()
        if not question:
            raise HTTPException(400, "질문을 입력하세요.")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute("SELECT * FROM chat_sessions WHERE id=? AND owner=?",
                                   (session_id, owner)).fetchone()
            if session is None:
                raise HTTPException(404, "대화를 찾을 수 없습니다.")
            rows = conn.execute("SELECT * FROM chat_messages WHERE session_id=? ORDER BY id",
                                (session_id,)).fetchall()
            message = next((row for row in rows if row["id"] == message_id), None)
            if message is None or message["role"] != "user":
                raise HTTPException(404, "수정할 질문을 찾을 수 없습니다.")
            if rows[-1]["role"] == "user":
                raise HTTPException(409, "답변이 끝난 뒤 수정해 주세요.")
            if message["content"] != expected or rows[-1]["id"] != tail_id:
                raise HTTPException(409, "대화가 변경되었습니다. 새로 확인한 뒤 수정해 주세요.")
            context = conn.execute("SELECT * FROM chat_message_context WHERE message_id=?",
                                   (message_id,)).fetchone()
            stored = replacement or (Path(context["attachment"]) if context and context["attachment"] else None)
            attached = message["content"].startswith("[첨부]")
            if (attached or stored) and (stored is None or not stored.is_file()):
                raise HTTPException(400, "이전 첨부 파일을 찾을 수 없습니다. 파일을 다시 선택해 주세요.")
            criteria = json.loads(context["criteria"]) if context else []
            shown = question
            if replacement:
                shown = f"[첨부] {filename}\n{question}"
            elif attached:
                shown = message["content"].split("\n", 1)[0] + "\n" + question
            snapshot = [dict(row) for row in rows if row["id"] >= message_id]
            conn.execute("INSERT INTO chat_revisions(session_id,message_id,created_at,messages) VALUES(?,?,?,?)",
                         (session_id, message_id, datetime.now(timezone.utc).isoformat(),
                          json.dumps(snapshot, ensure_ascii=False)))
            conn.execute("DELETE FROM chat_messages WHERE session_id=? AND id>?", (session_id, message_id))
            conn.execute("UPDATE chat_messages SET content=? WHERE id=? AND session_id=?",
                         (shown, message_id, session_id))
            conn.execute("INSERT OR REPLACE INTO chat_message_context VALUES(?,?,?)",
                         (message_id, str(stored) if stored else None, json.dumps(criteria)))
            if rows[0]["id"] == message_id and session["title"] == message["content"][:60]:
                conn.execute("UPDATE chat_sessions SET title=? WHERE id=?", (question[:60], session_id))
        return question, stored, criteria


def install_routes(app, db, store, schedule_answer, is_running, inbox, allowed_extensions, sources):
    @app.get('/chat/{session_id}/revisions')
    def revisions(request: Request, session_id: int):
        session = db.get_chat_session(session_id)
        if not session or session.get('owner') != request.state.user:
            raise HTTPException(404, '대화를 찾을 수 없습니다.')
        return {'revisions': store.history(session_id)}

    @app.post('/chat/{session_id}/messages/{message_id}/edit')
    def edit_question(request: Request, background: BackgroundTasks, session_id: int,
                      message_id: int, question: str = Form(...), expected_content: str = Form(...),
                      expected_tail_id: int = Form(...), attachment: UploadFile | None = File(None)):
        import shutil
        import uuid

        session = db.get_chat_session(session_id)
        if not session or session.get('owner') != request.state.user:
            raise HTTPException(404, '대화를 찾을 수 없습니다.')
        if is_running(session_id):
            raise HTTPException(409, '답변이 끝난 뒤 수정해 주세요.')
        stored = None
        if attachment and attachment.filename:
            suffix = Path(attachment.filename).suffix.lower()
            if suffix not in allowed_extensions:
                raise HTTPException(400, '허용되지 않는 파일 형식입니다.')
            stored = inbox / f'chat_{uuid.uuid4().hex}{suffix}'
            with stored.open('wb') as output:
                shutil.copyfileobj(attachment.file, output)
        try:
            question, stored, criteria = store.edit(
                session_id, message_id, request.state.user, question, expected_content,
                expected_tail_id, stored, attachment.filename if attachment else '',
            )
        except Exception:
            if stored is not None:
                stored.unlink(missing_ok=True)  # 이 요청에서 새로 저장한 파일만 정리
            raise
        sources.pop(session_id, None)
        schedule_answer(background, session_id, question, stored, criteria)
        return {'ok': True, 'session_id': session_id}
