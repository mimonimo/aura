"""계정별 통합 채팅 기록. 보관은 가역적인 목록 상태이며 대화를 삭제하지 않는다."""
from contextlib import closing
import re
import sqlite3
from .database_backend import connect, table_names, lock_session

from fastapi import Form, HTTPException, Request


_PROJECT_PREFIX = re.compile(r"^\[([^\]\n]{1,60})\]\s*")


def display_title(title: str | None, project_name: str | None = None) -> str:
    """화면에 보일 대화 제목 — 전각 영문·숫자(ＡＩＤ)를 반각으로, 공백을 한 칸으로.

    프로젝트 안에서 보이는 목록이면(project_name) 제목 앞의 「[프로젝트 이름…] 」 접두어를 뗀다. 접두어는
    새 대화를 만들 때 프로젝트 이름 앞부분을 잘라 붙인 것이라, 프로젝트 이름이 그 글자로 시작할 때만 뗀다."""
    from zzaimy.app.responder import normalize_input

    text = " ".join(normalize_input(title or "").split())
    if project_name:
        m = _PROJECT_PREFIX.match(text)
        name = " ".join(normalize_input(project_name).split())
        head = m.group(1).strip().rstrip("…") if m else ""
        if m and head and name.startswith(head) and text[m.end():].strip():
            text = text[m.end():].strip()
    return text or "대화"


def query_forms(query: str) -> list[str]:
    """대화 검색어의 표기 변형 — 반각으로 정리한 말과 그 전각 표기(대·소문자). 저장된 제목·글이 전각(ＡＩＤ)이어도
    반각(AID)으로 찾고, 그 반대도 된다. 대화 검색 창과 머리말 검색이 이 하나로 맞춘다(SQL 의 lower 는 전각을 접지 않는다)."""
    from zzaimy.app.responder import normalize_input

    half = " ".join(normalize_input(query or "").split())
    if not half:
        return []
    def wide(t: str) -> str:
        return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else ("\u3000" if c == " " else c) for c in t)
    forms = [half] + [wide(t) for t in (half, half.upper(), half.lower())]
    return list(dict.fromkeys(f for f in forms if f))


class ChatHistory:
    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS chat_session_preferences (
                    session_id INTEGER PRIMARY KEY, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS chat_messages_session_id ON chat_messages(session_id, id);
                CREATE INDEX IF NOT EXISTS chat_sessions_owner_id ON chat_sessions(owner, id);
            ''')

    def connect(self):
        return connect(self.path)

    def sessions(self, owner, query='', archived=False, offset=0, limit=30, topic_match=None, scope='title'):
        """계정의 대화 목록. topic_match 는 주제(근거 문서의 사업·규정 이름) 검색 조건을 함께 건다.

        주제 조건도 같은 질의 안에서 걸러야 정렬·페이지 나눔이 한 결과집합에 적용된다.
        첫 페이지에서만 합치면 다음 페이지에서 빠진다.
        """
        forms = query_forms(query)
        match_sql, match_args = '', []
        for f in forms:
            match_sql += " OR instr(lower(s.title), lower(?))>0 OR instr(lower(COALESCE(p.name,'')), lower(?))>0"
            match_args += [f, f]
        if forms and topic_match:
            match_sql += f' OR ({topic_match[0]})'
            match_args += list(topic_match[1])
        if forms and scope == 'all':
            # 대화 내용까지 — 주고받은 글에서 찾는다(맥락 검색)
            for f in forms:
                match_sql += (" OR EXISTS (SELECT 1 FROM chat_messages m2 WHERE m2.session_id = s.id"
                              " AND instr(lower(m2.content), lower(?)) > 0)")
                match_args.append(f)
        where_q = f" AND (0=1{match_sql})" if forms else ""
        with closing(self.connect()) as conn:
            rows = conn.execute(f'''
                SELECT s.*, p.name AS project_name,
                    COALESCE((SELECT MAX(m.id) FROM chat_messages m WHERE m.session_id=s.id), 0) AS last_message
                FROM chat_sessions s
                LEFT JOIN projects p ON p.id=s.project_id
                LEFT JOIN chat_session_preferences pref ON pref.session_id=s.id
                WHERE s.owner=? AND COALESCE(pref.archived,0)=?{where_q}
                ORDER BY last_message DESC, s.id DESC LIMIT ? OFFSET ?
            ''', (owner, int(archived), *match_args, limit, offset)).fetchall()
        return [dict(row) | {'title': display_title(row['title'])} for row in rows]

    def change(self, owner, session_id, title=None, archived=None):
        with closing(self.connect()) as conn, conn:
            if not conn.execute('SELECT 1 FROM chat_sessions WHERE id=? AND owner=?', (session_id, owner)).fetchone():
                raise HTTPException(404, '대화를 찾을 수 없습니다.')
            if title is not None:
                title = display_title(title) if (title or '').strip() else ''
                if not title or len(title)>80:
                    raise HTTPException(400, '대화 이름을 1~80자로 입력하세요.')
                conn.execute('UPDATE chat_sessions SET title=? WHERE id=?', (title, session_id))
            if archived is not None:
                conn.execute('INSERT INTO chat_session_preferences VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET archived=excluded.archived', (session_id, int(archived)))

    def delete(self, owner, session_id):
        with closing(self.connect()) as conn, conn:
            lock_session(conn, session_id)
            if not conn.execute('SELECT 1 FROM chat_sessions WHERE id=? AND owner=?', (session_id, owner)).fetchone():
                raise HTTPException(404, '대화를 찾을 수 없습니다.')
            last = conn.execute('SELECT role FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 1', (session_id,)).fetchone()
            if last and last['role'] == 'user':
                raise HTTPException(409, '답변이 끝난 뒤 삭제해 주세요.')
            # Context tables are installed separately. Never delete shared documents or files.
            tables = table_names(conn)
            if 'chat_message_context' in tables:
                conn.execute('DELETE FROM chat_message_context WHERE message_id IN (SELECT id FROM chat_messages WHERE session_id=?)', (session_id,))
            if 'chat_revisions' in tables:
                conn.execute('DELETE FROM chat_revisions WHERE session_id=?', (session_id,))
            if 'chat_sources' in tables:      # 대화의 근거 기록(chat_topics.py)도 같은 트랜잭션에서
                conn.execute('DELETE FROM chat_sources WHERE session_id=?', (session_id,))
            conn.execute('DELETE FROM chat_messages WHERE session_id=?', (session_id,))
            conn.execute('DELETE FROM chat_session_preferences WHERE session_id=?', (session_id,))
            conn.execute('DELETE FROM chat_sessions WHERE id=?', (session_id,))


def install_routes(app, history):
    @app.delete('/api/chat/sessions/{session_id}')
    def delete(request: Request, session_id: int):
        history.delete(request.state.user, session_id)
        return {'ok': True}

    @app.get('/api/chat/sessions')
    def sessions(request: Request, q: str='', archived: bool=False, offset: int=0, scope: str='title'):
        topics = getattr(history, 'topics', None)
        # 사업 이름으로도 찾는다 — 제목에 없어도 근거 문서의 사업이 맞으면 나온다(한 질의 안에서)
        match = topics.match_clause(q[:200]) if (topics is not None and q.strip()) else None
        rows = history.sessions(request.state.user, q[:200], archived, max(0,offset), 31, match,
                                'all' if scope == 'all' else 'title')
        if topics is not None:  # 근거 문서에서 정한 대화 주제(chat_topics.py)
            names = topics.topics([r['id'] for r in rows])
            for r in rows:
                r['topic'] = names.get(r['id'])
        return {'sessions': rows[:30], 'has_more': len(rows)>30}

    @app.post('/api/chat/sessions/{session_id}')
    def change(request: Request, session_id: int, title: str | None=Form(None), archived: bool | None=Form(None)):
        history.change(request.state.user, session_id, title, archived)
        return {'ok': True}
