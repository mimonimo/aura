from tests.test_chat_workspace import candidate_client


def test_home_and_spaces_keep_work_tools(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    home = client.get('/', follow_redirects=False)
    assert home.status_code == 303 and home.headers['location'] == '/chat'
    assert 'chatWorkspace' in client.get('/').text
    for route in ('/inbox', '/connections', '/criteria', '/ocr', '/dev'):
        page = client.get(route)
        assert page.status_code == 200
        assert '/static/platform-spaces.css' in page.text
    connections = client.get('/connections').text
    assert '미지원 · 연결되지 않음' in connections
    assert 'href="/dev/nas"' in connections
    assert '일반휴학 처리 기준' not in client.get('/chat').text


def test_delete_chat_is_owner_scoped_and_preserves_project(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    db = client.app.state.db
    pid = db.create_project('grant', '남겨둘 프로젝트')
    sid = db.create_chat_session('삭제할 대화', project_id=pid)
    other = db.create_chat_session('다른 계정', owner='someone')
    assert client.delete(f'/api/chat/sessions/{other}').status_code == 404
    db.add_chat(sid, 'user', '질문')
    assert client.delete(f'/api/chat/sessions/{sid}').status_code == 409
    db.add_chat(sid, 'assistant', '답변')
    assert client.post(f'/api/chat/sessions/{sid}', data={'archived':'true'}).status_code == 200
    assert client.delete(f'/api/chat/sessions/{sid}').status_code == 200
    assert db.get_chat_session(sid) is None
    assert db.list_chats(sid) == []
    assert db.get_chat_session(other) is not None
    assert client.get(f'/project/{pid}').status_code == 200
    assert client.get(f'/chat/{sid}').status_code == 404
    assert client.delete(f'/api/chat/sessions/{sid}').status_code == 404


def test_history_rename_archive_restore_is_owner_scoped(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    db = client.app.state.db
    sid = db.create_chat_session('내 대화')
    other = db.create_chat_session('다른 계정', owner='someone')
    assert [s['id'] for s in client.get('/api/chat/sessions').json()['sessions']] == [sid]
    assert client.post(f'/api/chat/sessions/{other}', data={'title':'수정'}).status_code == 404
    assert client.get(f'/chat/{other}/messages').status_code == 404
    assert client.post('/chat/ask', data={'session_id':other, 'question':'질문'}).status_code == 404
    assert client.post(f'/api/chat/sessions/{sid}', data={'title':'사업 검토'}).status_code == 200
    assert client.get('/api/chat/sessions?q=사업').json()['sessions'][0]['title'] == '사업 검토'
    assert client.post(f'/api/chat/sessions/{sid}', data={'archived':'true'}).status_code == 200
    assert client.get('/api/chat/sessions').json()['sessions'] == []
    assert client.get('/api/chat/sessions?archived=true').json()['sessions'][0]['id'] == sid
    assert db.get_chat_session(sid) is not None
    assert client.post(f'/api/chat/sessions/{sid}', data={'archived':'false'}).status_code == 200
    assert len(client.get('/api/chat/sessions').json()['sessions']) == 1


def test_widget_project_boundary_and_new_project_chat(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    db = client.app.state.db
    first = db.create_project('grant', '사업 A')
    second = db.create_project('grant', '사업 B')
    data = client.post('/chat/ask', data={'question':'사업 질문', 'page':f'/project/{first}'}).json()
    sid = data['session_id']
    assert db.get_chat_session(sid)['project_id'] == first
    response = client.post('/chat/ask', data={'question':'다른 사업', 'page':f'/project/{second}', 'session_id':sid})
    assert response.status_code == 409
    page = client.get(f'/chat?project={second}').text
    assert '사업 B' in page and f'name="project_id" value="{second}"' in page


def test_ui_initial_state_and_project_tools_remain_available(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    chat = client.get('/chat').text
    aside = chat.split('id="chatAside"', 1)
    assert 'chat-aside closed' in aside[0]
    assert 'aria-hidden="true" inert' in aside[1].split('>', 1)[0]
    assert 'data-async-submit' in chat
    assert 'id="sendBtn" disabled aria-label="보내기"' in chat
    library = client.get('/?type=all')
    assert library.status_code == 200 and '프로젝트 추가' in library.text
    assert 'href="/?type=all"' in chat
    db = client.app.state.db
    pid = db.create_project('grant', '업무 공간 검증')
    page = client.get(f'/project/{pid}').text
    for name in ('projectConversations', 'projectDocuments', 'projectContext'):
        assert f'id="{name}"' in page
    assert page.count('id="intakeForm"') == 1
    assert page.count('id="intakeFile"') == 1
    for action in ('/upload', '/chat/send', f'/project/{pid}/notes', f'/projects/{pid}/rename', '/criteria/upload'):
        assert f'action="{action}"' in page
    assert 'data-modal-open="#hwpTargetModal"' in page
    assert '일반휴학' not in page


def test_sidebar_has_one_entry_per_resource_function(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    page = client.get('/?type=all').text
    sidebar = page.split('id="workspaceSidebar"', 1)[1].split('</aside>', 1)[0]
    assert sidebar.count('data-chat-history') == 1
    assert 'href="/criteria"' not in sidebar
    assert 'href="/criteria"' not in page  # 자료 관리는 자료 연결에서 담당
    assert 'work-nav' not in sidebar
    assert '>처리 현황</div>' not in page
    assert '>접수 문서' not in page
    assert 'name="sector"' in page
    assert 'href="/criteria"' in client.get('/connections').text
    assert client.get('/criteria').status_code == 200


def test_project_library_keeps_creation_and_document_search(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    response = client.post('/projects', data={'sector': 'grant', 'name': '도서관 프로젝트', 'due_date': ''}, follow_redirects=False)
    assert response.status_code == 303
    library = client.get('/?type=all').text
    assert '도서관 프로젝트' in library
    assert 'name="sector"' in library
    assert '문서 검색 결과' not in library
    search = client.get('/?type=all&q=없는문서')
    assert search.status_code == 200 and '문서 검색 결과' in search.text
    connections = client.get('/connections').text.split('<div class="connection-grid">', 1)[1]
    assert 'href="/?type=all"' not in connections
    for route in ('/criteria', '/ocr', '/dev/nas'):
        assert f'href="{route}"' in connections
    assert 'data-page-back href="/connections"' in client.get('/criteria').text
