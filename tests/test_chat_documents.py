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


def test_folder_uses_actual_parent_and_checks_owner(client, monkeypatch):
    from types import SimpleNamespace
    from zzaimy.ingest import gdrive
    sid = attach(client)
    calls = []
    def metadata(self, url, **params):
        calls.append(url)
        return SimpleNamespace(json=lambda: {'parents': ['folderA']})
    monkeypatch.setattr(gdrive.GDriveBackend, '_get', metadata)
    assert client.get(f'/api/chat-documents/{sid}/folder').json() == {
        'url': 'https://drive.google.com/drive/folders/folderA'}
    client.app.state.owner = 'other'
    assert client.get(f'/api/chat-documents/{sid}/folder').status_code == 404
    assert len(calls) == 1


def test_file_list_reads_parent_and_never_crosses_owner(client, monkeypatch):
    from types import SimpleNamespace
    from zzaimy.ingest import gdrive
    sid = attach(client)
    monkeypatch.setattr(gdrive.GDriveBackend, '_get', lambda *a, **kw:
                        SimpleNamespace(json=lambda: {'parents': ['folderA']}))
    calls = []
    def children(self, folder):
        calls.append(folder)
        return [{'id': 'docA', 'name': '시험 문서', 'mimeType': 'application/vnd.google-apps.document'}]
    monkeypatch.setattr(gdrive.GDriveBackend, '_children', children)
    result = client.get(f'/api/chat-documents/{sid}/files')
    assert result.status_code == 200
    assert result.json()['files'][0]['name'] == '시험 문서'
    assert calls == ['folderA']
    client.app.state.owner = 'other'
    assert client.get(f'/api/chat-documents/{sid}/files').status_code == 404
    assert calls == ['folderA']


def test_file_list_without_binding_is_empty(client):
    sid = attach(client)
    client.delete(f'/api/chat-documents/{sid}')
    assert client.get(f'/api/chat-documents/{sid}/files').json()['files'] == []


def test_browse_connected_account_folder_and_pagination(client, monkeypatch):
    from types import SimpleNamespace
    from zzaimy.ingest import gdrive
    calls = []
    def request(self, url, **params):
        calls.append(params)
        return SimpleNamespace(json=lambda: {'files': [{'id': 'folderA', 'name': '사업', 'mimeType': gdrive.FOLDER}], 'nextPageToken': 'next'})
    monkeypatch.setattr(gdrive.GDriveBackend, '_get', request)
    result = client.get('/api/chat-documents/browse', params={'account': 'staff@example.ac.kr', 'folder': 'root'})
    assert result.status_code == 200
    assert result.json()['next'] == 'next'
    assert "'root' in parents" in calls[0]['q']
    client.get('/api/chat-documents/browse', params={'account': 'staff@example.ac.kr', 'folder': 'folderA', 'page': 'next'})
    assert calls[-1]['pageToken'] == 'next'
    assert client.get('/api/chat-documents/browse', params={'account': 'unknown'}).status_code == 400
    assert client.get('/api/chat-documents/browse', params={'account': 'staff@example.ac.kr', 'folder': "x' or true"}).status_code == 400
    assert len(calls) == 2
