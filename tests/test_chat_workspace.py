"""채팅 화면과 기존 API의 연동을 합성 대화로 검증한다."""
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import main


def candidate_client(tmp_path, monkeypatch):
    app = main.create_app(
        db_path=tmp_path / 'chat.db', inbox_dir=tmp_path / 'inbox',
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    return TestClient(app)


def test_edit_resend_preserves_original_history_and_project(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    client.post('/projects', data={'sector': 'grant', 'name': '합성 사업'})
    first = client.post('/chat/send', data={'question': '처음 질문', 'project_id': 1})
    assert first.status_code == 200
    assert 'data-edit-question="처음 질문"' in first.text
    second = client.post('/chat/send', data={'question': '수정한 질문', 'session_id': 1})
    assert second.status_code == 200
    rows = client.app.state.db.list_chats(1)
    assert [row['content'] for row in rows if row['role'] == 'user'] == ['처음 질문', '수정한 질문']
    assert client.app.state.db.get_chat_session(1)['project_id'] == 1


def test_question_attributes_are_escaped(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    payload = '\"><script>alert(1)</script>'
    page = client.post('/chat/send', data={'question': payload}).text
    assert '<script>alert(1)</script>' not in page
    assert 'data-edit-question="&#34;&gt;&lt;script&gt;' in page


def test_waiting_chat_allows_composing_next_question(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    db = client.app.state.db
    session = db.create_chat_session('합성 대화')
    db.add_chat(session, 'user', '대기 질문')
    page = client.get(f'/chat/{session}').text
    assert 'data-waiting="true"' in page
    input_tag = page.split('id="chatInput"', 1)[1].split('>', 1)[0]
    assert 'disabled' not in input_tag
    assert 'id="sendBtn" disabled' in page
    assert 'id="attachButton"' in page
    assert page.index('id="chatEditNote"') > page.index('id="chatForm"')
