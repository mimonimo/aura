"""「필요한 값 입력」 — 모름·나중에(차례 뒤로), 비운 칸은 보내지 않음, 한 열 목록 칸. 합성 값만 쓴다."""
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import asks, main
from zzaimy.app.db import Database


def _client(tmp_path):
    app = main.create_app(db_path=tmp_path / 'chat.db', inbox_dir=tmp_path / 'inbox',
                          processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return TestClient(app)


def _asks(*names):
    return [{'name': n, 'hint': ''} for n in names]


def test_defer_moves_names_behind_and_off_this_form(tmp_path):
    db = Database(tmp_path / 't.db')
    asks.set_pending(db, 1, _asks('값가', '값나', '값다'))
    assert asks.defer(db, 1, ['값다', '물은 적 없는 값']) == ['값다']               # 물었던 이름만
    assert [a['name'] for a in asks.pending(db, 1)] == ['값다', '값가', '값나']       # 칸은 끝부터 — 앞자리가 맨 뒤 차례
    opt = asks.form_option(asks.pending(db, 1))
    assert [f['name'] for f in opt['fields']] == ['값가', '값나'] and opt['more'] == 1 and opt['later'] == 1
    # 다른 값이 다 차면 미룬 값이 다음 차례에 나온다
    asks.set_pending(db, 1, [a for a in asks.pending(db, 1) if a['name'] == '값다'])
    assert [f['name'] for f in asks.form_option(asks.pending(db, 1))['fields']] == ['값다']
    # 새로 미루면 앞서 미룬 표시는 풀린다
    asks.set_pending(db, 1, _asks('값가', '값나'))
    asks.defer(db, 1, ['값가']); asks.defer(db, 1, ['값나'])
    assert [a.get('later', False) for a in asks.pending(db, 1)] == [True, False]
    # 새 값을 물으면 미룬 값도 다시 차례를 다툰다
    asks.add_pending(db, 1, _asks('값라'))
    assert not any(a.get('later') for a in asks.pending(db, 1))


def test_more_than_six_keeps_deferred_out_until_rest_done():
    pend = [{'name': f'값{i}', 'hint': ''} for i in range(8)]
    pend[0]['later'] = True
    opt = asks.form_option(pend)
    assert [f['name'] for f in opt['fields']] == [f'값{i}' for i in range(2, 8)] and opt['more'] == 2 and opt['later'] == 1


def test_send_defers_blank_fields(tmp_path):
    """비워 두고 저장한 칸은 따로 「나중에」 를 누르지 않아도 다음 차례로 간다."""
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    db.add_chat(sid, 'assistant', '빈 값이 있습니다.')
    asks.set_pending(db, sid, _asks('가상 이름', '가상 기간', '가상 수량'))
    opt = asks.form_option(asks.pending(db, sid))
    later = lambda: {a['name'] for a in asks.pending(db, sid) if a.get('later')}
    # 모두 비우고 「모두 다음에 받기」 — 대화로 보낼 값이 없으니 메시지 없이 돌아오고 칸들은 뒤로
    r = client.post('/chat/send', data={'session_id': sid, 'question': opt['question'], 'template': opt['template'],
                                        'field_가상 이름': '', 'field_가상 기간': ' '}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/chat/{sid}'
    assert later() == {'가상 이름', '가상 기간'} and len(db.list_chats(sid)) == 1
    form = client.get(f'/chat/{sid}').text.split('id="chatValueForm"', 1)[1].split('</form>', 1)[0]
    assert 'data-field="가상 기간"' not in form and '이어서 2개(미룬 값 2개 포함)' in form
    # 값이 있으면 그 값만 보내고 비운 칸은 뒤로(예전 화면이 보내는 later 도 그대로 받는다)
    client.post('/chat/send', data={'session_id': sid, 'question': opt['question'], 'template': opt['template'],
                                    'field_가상 이름': '합성 사업단', 'field_가상 수량': '', 'later': ['가상 이름']})
    assert db.list_chats(sid)[1]['content'] == '가상 이름: 합성 사업단'
    assert '가상 수량' in later() and '가상 이름' not in {a['name'] for a in asks.pending(db, sid)}


def test_value_form_rows_have_pick_hint_and_source(tmp_path):
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    db.add_chat(sid, 'assistant', '빈 값이 있습니다.')
    asks.set_pending(db, sid, [{'name': '가상 기간', 'hint': '협약 기간'}, {'name': '가상 이름', 'hint': '표지 이름'}])
    import json
    db.set_setting(f'chat_facts:{sid}:suggested', json.dumps({'가상 기간': {'value': '2099. 1. 1.', 'title': '가상 공고'}}, ensure_ascii=False))
    form = client.get(f'/chat/{sid}').text.split('id="chatValueForm"', 1)[1].split('</form>', 1)[0]
    assert form.count('data-pick ') == 2 and 'data-pick-all' in form and 'name="later"' not in form   # 비우면 저절로 다음 차례
    assert 'is-filled is-suggested' in form and 'data-accept-value>확인</button>' in form and 'data-edit-value>고치기</button>' in form
    assert '자료에서 찾음 · 확인 전 · 《가상 공고》' in form and 'title="협약 기간"' in form   # 설명은 원래 설명, 출처는 따로
    assert '2개 중 1개 채움' in form and 'data-form-submit>확인</button>' in form


def test_form_values_are_remembered_directly_without_the_agent(tmp_path, monkeypatch):
    """칸에 적어 보낸 값은 물었던 값의 대답이다 — 모델에게 해석을 맡기지 않고 그대로 기억하고 받을 값 목록에서 뺀다."""
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    asks.set_pending(db, sid, _asks('가상 사업단명', '가상 책임자'))
    r = client.post('/chat/send', data={'session_id': str(sid), 'question': '가상 사업단명: (값); 가상 책임자: (값)',
                                        'template': '가상 사업단명: {가상 사업단명}; 가상 책임자: {가상 책임자}',
                                        'field_가상 사업단명': '가나 사업단', 'field_가상 책임자': ''},
                    follow_redirects=False)
    assert r.status_code == 303
    assert [a['name'] for a in asks.pending(db, sid)] == ['가상 책임자']            # 채운 값만 빠진다
    assert asks.facts(db, db.get_chat_session(sid))['가상 사업단명'] == '가나 사업단'
    msgs = [m['content'] for m in db.list_chats(sid)]
    assert any('가나 사업단' in m for m in msgs) and any(m.startswith('기억했습니다') for m in msgs)
