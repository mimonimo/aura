import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from tests.test_gdocs import docs_env
from zzaimy.app.db import Database
from zzaimy.app import chat_documents


@pytest.fixture
def client(docs_env, tmp_path):
    app = FastAPI()
    app.state.db = Database(tmp_path/'chat.db')
    app.state.owner = 'owner'
    @app.middleware('http')
    async def identity(request: Request, next_call):
        request.state.user = app.state.owner
        return await next_call(request)
    app.include_router(chat_documents.router)
    return TestClient(app)


def attach(client):
    result = client.post('/api/chat-documents/connect', data={'doc':'docA','account':'staff@example.ac.kr'})
    assert result.status_code == 200
    return result.json()['session_id']


def test_connect_persist_material_disconnect(client):
    sid = attach(client)
    assert client.app.state.db.get_chat_session(sid)['owner'] == 'owner'
    result = client.get(f'/api/chat-documents/{sid}').json()
    assert result['connected'] and result['sections'][1]['heading'] == '1. 추진 배경'
    assert '지역 산업 수요' in chat_documents.material(client.app.state.db, sid, 'owner')
    assert client.delete(f'/api/chat-documents/{sid}').status_code == 200
    assert not client.get(f'/api/chat-documents/{sid}').json()['connected']
    assert client.app.state.db.get_chat_session(sid)
    assert chat_documents.material(client.app.state.db, sid, 'owner') == ''


def test_foreign_session_and_project_rejected_before_read(client, docs_env):
    sid = attach(client)
    project = client.app.state.db.create_project('grant','비공개',owner='owner')
    client.app.state.owner = 'other'
    calls = len(docs_env[1])
    assert client.get(f'/api/chat-documents/{sid}').status_code == 404
    assert client.delete(f'/api/chat-documents/{sid}').status_code == 404
    assert client.post('/api/chat-documents/connect',data={'doc':'docA','account':'staff@example.ac.kr','session_id':sid}).status_code == 404
    assert client.post('/api/chat-documents/connect',data={'doc':'docA','account':'staff@example.ac.kr','project_id':project}).status_code == 404
    assert len(docs_env[1]) == calls


def test_insert_requires_confirmation_and_preserves_audit(client, docs_env):
    sid = attach(client)
    data = {'section':2,'text':'새 내용'}
    assert client.post(f'/api/chat-documents/{sid}/insert',data=data).status_code == 400
    assert not any(c[1].endswith(':batchUpdate') for c in docs_env[1])
    assert client.post(f'/api/chat-documents/{sid}/insert',data={**data,'confirmed':'true'}).status_code == 200
    assert any(c[1].endswith(':batchUpdate') for c in docs_env[1])


def test_bad_document_does_not_replace_binding(client):
    sid = attach(client)
    assert client.post('/api/chat-documents/connect',data={'session_id':sid,'doc':'missing','account':'staff@example.ac.kr'}).status_code == 400
    assert client.get(f'/api/chat-documents/{sid}').json()['doc'] == 'docA'
