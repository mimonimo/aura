import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from zzaimy.app.database_backend import connect, placeholders, postgres_config, Row


def test_parameter_conversion_preserves_literals():
    assert placeholders("SELECT '?' AS x, ? -- ?\n WHERE name LIKE '%한글%'") == "SELECT '?' AS x, %s -- ?\n WHERE name LIKE '%%한글%%'"
    row = Row(['id', 'name'], (4, '한글'))
    assert row[0] == row['id'] == 4
    assert dict(row) == {'id': 4, 'name': '한글'}
    assert tuple(row) == (4, '한글')


def test_other_database_never_uses_platform_dsn(monkeypatch, tmp_path):
    monkeypatch.setenv('ZZAIMY_DATABASE_URL', 'invalid-do-not-connect')
    monkeypatch.setenv('ZZAIMY_PLATFORM_SQLITE_PATH', str(tmp_path / 'platform.db'))
    assert postgres_config(tmp_path / 'corpus.db') is None
    with pytest.raises(ValueError):
        postgres_config(tmp_path / 'platform.db')


@pytest.fixture
def pg_runtime(monkeypatch, tmp_path):
    dsn = os.environ.get('ZZAIMY_TEST_MIGRATION_DSN')
    if not dsn:
        pytest.skip('requires dedicated PostgreSQL test DB')
    psycopg = pytest.importorskip('psycopg')
    schema = 'aura_test_' + uuid.uuid4().hex[:20]
    with psycopg.connect(dsn) as pg:
        pg.execute(psycopg.sql.SQL('CREATE SCHEMA {}').format(psycopg.sql.Identifier(schema)))
    path = tmp_path / 'platform.db'
    monkeypatch.setenv('ZZAIMY_DATABASE_URL', dsn)
    monkeypatch.setenv('ZZAIMY_DATABASE_SCHEMA', schema)
    monkeypatch.setenv('ZZAIMY_PLATFORM_SQLITE_PATH', str(path))
    try:
        yield path
    finally:
        with psycopg.connect(dsn) as pg:
            pg.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(schema)))


def test_postgres_runtime_documents_chat_search_revision(pg_runtime):
    from zzaimy.app.db import Database
    from zzaimy.app.chat_history import ChatHistory
    from zzaimy.app.chat_topics import ChatTopics
    from zzaimy.app.chat_revisions import ChatRevisions
    from zzaimy.app.project_search import search
    db = Database(pg_runtime)
    # Idempotent startup/migrations must not leave PG's transaction aborted.
    Database(pg_runtime)
    history, topics, revisions = ChatHistory(pg_runtime), ChatTopics(pg_runtime), ChatRevisions(pg_runtime)
    project = db.create_project('grant', '검증 사업')
    doc = db.add_document('한글 기준.pdf', '/fixture.pdf', 'regulation', project_id=project)
    db.set_project_criteria(project, [doc, doc])
    assert db.get_project_criteria_ids(project) == [doc]
    db.set_setting('test', '한글 ? 100%')
    db.set_setting('test', '수정')
    assert db.get_setting('test') == '수정'
    db.replace_doc_entities(doc, [('검증사업', 'program', 1)])
    assert db.graph_entities(1)['entities']
    session = db.create_chat_session('질문', project_id=project)
    db.add_chat(session, 'user', '질문')
    db.add_chat(session, 'assistant', '답변')
    rows = db.list_chats(session)
    revisions.remember(rows[0]['id'], None, [doc])
    revisions.edit(session, rows[0]['id'], 'zzaimy', '수정 질문', '질문', rows[-1]['id'])
    assert db.list_chats(session)[0]['content'] == '수정 질문'
    assert revisions.history(session)
    db.add_chat(session, 'assistant', '수정 답변')
    topics.record(session, [{'doc_id': doc, 'title': '사업 근거'}])
    assert topics.latest(session)[0]['doc_id'] == doc
    assert history.sessions('zzaimy', '수정')
    assert history.sessions('zzaimy', '사업', topic_match=topics.match_clause('사업'))
    assert search(db, 'zzaimy', '검증')['projects'][0]['id'] == project
    history.delete('zzaimy', session)
    assert db.get_chat_session(session) is None
    assert not pg_runtime.exists()  # no accidental shadow SQLite


def test_postgres_concurrent_receipts_and_readonly(pg_runtime):
    import psycopg
    from zzaimy.app.db import Database
    db = Database(pg_runtime)
    with ThreadPoolExecutor(max_workers=4) as workers:
        ids = list(workers.map(lambda i: db.add_document(f'문서{i}.pdf', '/fixture.pdf'), range(12)))
    receipts = [db.get_document(i)['receipt_no'] for i in ids]
    assert len(set(ids)) == len(set(receipts)) == 12
    with connect(pg_runtime, readonly=True) as conn:
        assert conn.execute('SELECT count(*) FROM documents').fetchone()[0] == 12
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM documents")
