from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from zzaimy.app.db import Database
from zzaimy.app.project_search import router, search


def make_db(tmp_path):
    return Database(tmp_path / 'projects.db')


def test_search_finds_old_projects_and_paginates_without_other_accounts(tmp_path):
    db = make_db(tmp_path)
    old = db.create_project('grant', '오래된 장학사업', owner='owner')
    for i in range(35):
        db.create_project('grant', f'사업 {i}', owner='owner')
    db.create_project('grant', '타 계정 비공개', owner='other')
    assert search(db, 'owner', '오래된')['projects'][0]['id'] == old
    first, second = search(db, 'owner'), search(db, 'owner', offset=30)
    assert first['has_more'] and not second['has_more']
    assert len(first['projects']) == 30 and len(second['projects']) == 6
    assert len({p['id'] for p in first['projects'] + second['projects']}) == 36
    assert not search(db, 'owner', '비공개')['projects']


def test_literal_search_and_sector(tmp_path):
    db = make_db(tmp_path)
    db.create_project('grant', '100%_사업', owner='owner')
    db.create_project('admission', '입시 준비', owner='owner')
    assert len(search(db, 'owner', '%_')['projects']) == 1
    assert len(search(db, 'owner', '국고사업')['projects']) == 1
    assert not search(db, 'owner', "' OR 1=1 --")['projects']


def test_endpoint_requires_identity_and_validates_query(tmp_path):
    app = FastAPI()
    app.state.db = make_db(tmp_path)
    app.include_router(router)
    client = TestClient(app)
    assert client.get('/api/projects/search').status_code == 401
    assert client.get('/api/projects/search?offset=-1').status_code == 422
    assert client.get('/api/projects/search', params={'q':'a'*201}).status_code == 422


def test_endpoint_scopes_results_and_disables_cache(tmp_path):
    app = FastAPI()
    app.state.db = make_db(tmp_path)
    app.state.db.create_project('grant', '<script>사업</script>', owner='owner')
    app.state.db.create_project('grant', '비공개', owner='other')
    @app.middleware('http')
    async def identity(request: Request, call_next):
        request.state.user = 'owner'
        return await call_next(request)
    app.include_router(router)
    result = TestClient(app).get('/api/projects/search')
    assert result.status_code == 200
    assert len(result.json()['projects']) == 1
    assert result.headers['cache-control'] == 'no-store'


def test_search_also_finds_by_document_and_chat_titles(tmp_path):
    db = make_db(tmp_path)
    pid = db.create_project('grant', '가나', owner='owner')
    db.add_document('2099년 다라 부트캠프 사업 계획', str(tmp_path / 'a.pdf'), doc_type='grant', project_id=pid, owner='owner')
    other = db.create_project('grant', '마바', owner='owner')
    db.create_chat_session('사아 공고 검토', project_id=other, owner='owner')
    named = db.create_project('grant', '부트캠프 준비', owner='owner')
    got = search(db, 'owner', '부트캠프')['projects']
    assert [p['id'] for p in got] == [named, pid]                 # 이름에서 찾은 것이 먼저
    assert got[1]['matched'] == 'document' and got[0]['matched'] == 'name'
    assert search(db, 'owner', '사아 공고')['projects'][0]['matched'] == 'chat'
    db.add_document('남의 부트캠프', str(tmp_path / 'b.pdf'), doc_type='grant', project_id=pid, owner='other')
    assert len(search(db, 'other', '부트캠프')['projects']) == 0    # 남의 프로젝트는 문서가 있어도 안 보인다
