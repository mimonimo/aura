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
    assert '<a href="/project/1">합성 사업</a>' in first.text
    assert '최근 요청 · 처음 질문' in first.text
    second = client.post('/chat/send', data={'question': '수정한 질문', 'session_id': 1})
    assert second.status_code == 200
    assert '최근 요청 · 수정한 질문' in second.text
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
    assert '<h1 title="합성 대화">합성 대화</h1>' in page
    assert '최근 요청 · 대기 질문' in page
    input_tag = page.split('id="chatInput"', 1)[1].split('>', 1)[0]
    assert 'disabled' not in input_tag
    assert 'id="sendBtn" disabled' in page
    assert 'id="attachButton"' in page
    assert page.index('id="chatEditNote"') > page.index('id="chatForm"')


def test_direct_edit_keeps_id_and_archives_following_conversation(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    client.post('/chat/send', data={'question': '처음 질문'})
    client.post('/chat/send', data={'question': '후속 질문', 'session_id': 1})
    db = client.app.state.db
    before = db.list_chats(1)
    response = client.post(f'/chat/1/messages/{before[0]["id"]}/edit', data={
        'question': '바로 고친 질문', 'expected_content': before[0]['content'],
        'expected_tail_id': before[-1]['id'],
    })
    assert response.status_code == 200, response.text
    after = db.list_chats(1)
    assert len(after) == 2
    assert after[0]['id'] == before[0]['id']
    assert after[0]['content'] == '바로 고친 질문'
    assert after[1]['role'] == 'assistant'
    assert db.get_chat_session(1)['title'] == '바로 고친 질문'
    history = client.get('/chat/1/revisions').json()['revisions']
    assert history[0]['messages'] == before


def test_direct_edit_rejects_stale_waiting_and_foreign_session(tmp_path, monkeypatch):
    client = candidate_client(tmp_path, monkeypatch)
    client.post('/chat/send', data={'question': '원문'})
    db = client.app.state.db
    rows = db.list_chats(1)
    url = f'/chat/1/messages/{rows[0]["id"]}/edit'
    payload = {'question': '수정', 'expected_content': '다른 원문', 'expected_tail_id': rows[-1]['id']}
    assert client.post(url, data=payload).status_code == 409
    payload['expected_content'] = '원문'
    payload['question'] = ' '
    assert client.post(url, data=payload).status_code == 400
    assert db.list_chats(1) == rows
    other = db.create_chat_session('다른 계정', owner='other')
    assert client.get(f'/chat/{other}/revisions').status_code == 404
    assert client.post(f'/chat/{other}/messages/1/edit', data=payload).status_code == 404
    db.add_chat(1, 'user', '작성 중')
    payload['question'] = '수정'
    assert client.post(url, data=payload).status_code == 409
    assert client.get('/chat/1/revisions').json() == {'revisions': []}


def test_chat_topic_comes_from_cited_documents(tmp_path):
    """대화 주제는 답변이 근거로 쓴 문서에서 정한다 — 약한 근거보다 강한 근거가 이긴다."""
    import json

    from zzaimy.app.chat_topics import ChatTopics
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    d1 = db.add_document(filename="law03.pdf", stored_path="", doc_type="regulation")
    db.update_document(d1, masked_text="영남이공대학교 산학협력단 사무분장 규정\n제 1 조(목적)")
    d2 = db.add_document(filename="공고.hwp", stored_path="", doc_type="regulation")
    db.set_doc_identity(d2, {"program": "2026 지방대학 특성화 선도대학 육성사업"})
    t = ChatTopics(tmp_path / "t.db")
    t.record(1, [{"doc_id": d1, "title": "law03.pdf", "weak": True},
                 {"doc_id": d2, "title": "공고.hwp"}])
    t.record(1, [{"doc_id": d2, "title": "공고.hwp"}])
    t.record(2, [{"doc_id": d1, "title": "law03.pdf"}])
    names = t.topics([1, 2, 3])
    assert names[1] == "2026 지방대학 특성화 선도대학 육성사업"
    assert names[2] == "영남이공대학교 산학협력단 사무분장 규정"
    assert 3 not in names
    t.record(4, [{"doc_id": d2, "title": "공고.hwp", "weak": True}])
    assert 4 not in t.topics([4])              # 약한 근거만 있으면 주제를 붙이지 않는다
    assert [s["title"] for s in t.latest(1)] == ["공고.hwp"]
    assert json.dumps(t.latest(2), ensure_ascii=False)


def test_chat_history_search_matches_topic(tmp_path):
    """대화 제목에 없어도 근거 문서의 사업 이름으로 기록을 찾는다."""
    from fastapi.testclient import TestClient

    from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
    from zzaimy.app.main import create_app

    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "in", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder())
    c = TestClient(app)
    db = app.state.db
    sid = db.create_chat_session("신청 자격 알려줘")
    d = db.add_document(filename="공고.hwp", stored_path="", doc_type="regulation")
    db.set_doc_identity(d, {"program": "2026 지방대학 특성화 선도대학 육성사업"})
    from zzaimy.app.chat_topics import ChatTopics
    ChatTopics(tmp_path / "t.db").record(sid, [{"doc_id": d, "title": "공고.hwp"}])
    got = c.get("/api/chat/sessions", params={"q": "특성화"}).json()["sessions"]
    assert [s["id"] for s in got] == [sid]
    assert got[0]["topic"] == "2026 지방대학 특성화 선도대학 육성사업"
