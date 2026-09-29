import json
import pytest
from fastapi.testclient import TestClient
from zzaimy.app.db import Database
from zzaimy.app import privacy_policy as policy
from zzaimy.app.main import create_app
from zzaimy.app.pipeline import DocumentProcessor, _masking_active
from zzaimy.dataset.privacy import approved_bytes, protect_candidate
from tests.test_app import FakeProcessor, FakeDrafter


def test_internal_policy_live_toggle_and_entity_choice(tmp_path):
    db = Database(tmp_path / 'p.db')
    proc = DocumentProcessor()
    proc.configure_privacy(db)
    text = '성명: 김민수 연락처 010-0000-0000'
    assert proc._mask_str(text) == text
    assert not _masking_active(proc._mask_str)
    policy.save(db, True, ['KR_PHONE'], 'tester')
    assert '김민수' in proc._mask_str(text) and '[KR_PHONE]' in proc._mask_str(text)
    assert not _masking_active(proc._mask_str)
    policy.save(db, True, ['KR_NAME'], 'tester')
    assert _masking_active(proc._mask_str)
    policy.save(db, False, ['KR_NAME'], 'tester')
    assert proc._mask_str(text) == text
    assert not _masking_active(proc._mask_str)
    # Internal off never disables training protection.
    assert '010-0000-0000' not in protect_candidate({'value': text})['value']


def test_invalid_policy_fails_closed(tmp_path):
    db = Database(tmp_path / 'p.db')
    for entities in ([], ['not-an-entity']):
        with pytest.raises(ValueError):
            policy.save(db, True, entities, 'tester')
    db.set_setting(policy.KEY, '{"enabled":true,"entities":[]}')
    with pytest.raises(ValueError):
        policy.load(db)


def test_staff_cannot_change_internal_policy(tmp_path):
    from tests.test_accounts import _app, _login
    app = _app(tmp_path)
    client = TestClient(app)
    assert _login(client, 'zzaimy', 'boot-pass-1')
    assert client.post('/dev/pii/policy', data={'enabled':'on','entities':'KR_NAME'}).status_code == 403
    assert policy.load(app.state.db)['enabled'] is False


def accepted_pair():
    return {'conversations': [{'from':'human','value':'근거: 교육과정 개선'},
                              {'from':'gpt','value':'교육과정 개선'}],
            'meta': {'program_id':'program-a','node_path':['사업','교육과정'],
                     'evidence_records':[{'program_id':'program-a','doc_id':1,'chunk_id':2,
                                          'turn':0,'text':'교육과정 개선'}],
                     'review': {'decision':'accept','reviewer':'tester',
                                'checks':dict.fromkeys(['grounding','structure','context','privacy'],True)}}}


def test_export_gate_exact_reviewed_bytes_and_pii(tmp_path):
    path = tmp_path / 'pairs.jsonl'
    pair = accepted_pair()
    path.write_text(json.dumps(pair, ensure_ascii=False)+'\n')
    assert approved_bytes(path) == path.read_bytes()
    pair['conversations'][0]['value'] += ' 연락처: 010-0000-0000'
    path.write_text(json.dumps(pair))
    with pytest.raises(ValueError):
        approved_bytes(path)
    pair = accepted_pair()
    pair['meta']['review']['checks']['privacy'] = False
    path.write_text(json.dumps(pair))
    with pytest.raises(ValueError):
        approved_bytes(path)


def test_policy_post_and_history_paging(tmp_path):
    app=create_app(db_path=tmp_path/'p.db', inbox_dir=tmp_path/'inbox',
                   processor=FakeProcessor(), drafter=FakeDrafter())
    client=TestClient(app)
    assert '꺼짐 · 원문 활용' in client.get('/dev/pii').text
    response=client.post('/dev/pii/policy', data={'enabled':'on','entities':'KR_PHONE'})
    assert response.status_code == 200 and '내부 마스킹 켜짐' in response.text
    assert client.post('/dev/pii/policy', data={'enabled':'on'}).status_code == 400
    db=app.state.db
    for i in range(13):
        doc=db.add_document(f'검증-{i}.pdf','/tmp/not-a-real-file',doc_type='grant')
        db.replace_mask_events(doc,[{'entity_type':'KR_PHONE','n':1,'context':'[KR_PHONE]'}])
    page=client.get('/dev/pii?view=history').text
    assert page.count('class="privacy-record"') == 10
    assert client.get('/dev/pii?view=history&page=2').text.count('class="privacy-record"') == 3
    filtered=client.get('/dev/pii?view=history&q=검증-12').text
    assert filtered.count('class="privacy-record"') == 1
