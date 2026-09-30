from copy import deepcopy
import json
import pytest
from zzaimy.dataset.authored_review import convert_tasks, upgrade_config, CHECK_LABELS
from zzaimy.dataset.privacy import approved_bytes


def task(sid='one'):
    record = {'id':sid, 'question':'교육과정의 목적은?', 'answer':'교육과정을 개선합니다.',
              'path':['교육과정'], 'refs':[[1,2]], 'source_texts':['교육과정을 개선합니다.']}
    fields = {'decision':['채택'], **{k:['확인'] for k in CHECK_LABELS}}
    return {'id':1, 'data': {'sample_id':sid, 'program':'예시 사업', 'question':record['question'],
            'answer':record['answer'], 'path':'교육과정', 'evidence':record['source_texts'][0],
            'history':'단독 질문', '_record':record},
            'annotations':[{'id':3,'completed_by':5,'result':[{'from_name':k,'to_name':'answer','value':{'choices':v}} for k,v in fields.items()]}]}


def convert(rows):
    return convert_tasks(rows, lambda d,c: '교육과정을 개선합니다.')


def test_accept_preserves_input_and_has_evidence():
    t = task(); before = deepcopy(t)
    pairs, report = convert([t])
    assert report['approved'] == 1 and report['held'] == 0
    assert pairs[0]['meta']['review']['reviewer'] == 'labelstudio:5'
    assert pairs[0]['meta']['evidence_records'][0]['chunk_id'] == 2
    assert t == before


@pytest.mark.parametrize('mutation,code', [
    (lambda t:t.update(annotations=[]), 'review_missing_or_ambiguous'),
    (lambda t:t['annotations'][0].update(was_cancelled=True), 'review_missing_or_ambiguous'),
    (lambda t:t['annotations'][0]['result'].pop(), 'review_incomplete'),
    (lambda t:t['data'].update(answer='다른 답'), 'display_record_mismatch'),
    (lambda t:t['data']['_record'].update(parent='absent'), 'parent_missing'),
])
def test_unapproved_or_tampered_held(mutation, code):
    t = task(); mutation(t)
    pairs, report = convert([t])
    assert not pairs and report['issues'] == {code:1}


def test_source_change_and_malformed_data_do_not_leak_text():
    assert convert_tasks([task()], lambda *a:None)[1]['issues'] == {'source_changed_or_missing':1}
    assert convert([None, {'data':'bad'}])[1]['held'] == 2
    def bad(*a): raise ValueError('private source text!')
    assert convert_tasks([task()], bad)[1]['issues'] == {'invalid_record':1}


def test_superseded_review_and_its_followup_are_not_exported():
    parent = task(); child = task('two')
    parent['data']['superseded_by'] = 'one-r2'
    child['data']['_record']['parent'] = 'one'
    pairs, report = convert([parent, child])
    assert not pairs
    assert report['issues'] == {'superseded_sample': 2}


def test_explicit_program_identity_is_preserved_and_cross_program_parent_held():
    parent = task(); child = task('two')
    for t, pid in ((parent,'program-a'),(child,'program-b')):
        t['data']['program_id'] = pid
        t['data']['_record']['program_id'] = pid
    child['data']['_record']['parent'] = 'one'
    pairs, report = convert([parent,child])
    assert pairs[0]['meta']['program_id'] == 'program-a'
    assert report['issues'] == {'cross_program_parent':1}
    parent['data']['_record']['program_id'] = 'changed'
    assert convert([parent])[1]['issues'] == {'invalid_program_identity':1}


def test_corrected_parent_invalidates_old_followup():
    parent = task(); child = task('two')
    history = [{'question':parent['data']['question'], 'answer':parent['data']['answer']}]
    child['data']['_record'].update(parent='one', history=history)
    child['data']['history'] = '질문: '+history[0]['question']+'\n답변: '+history[0]['answer']
    assert convert([parent,child])[1]['approved'] == 2
    parent['annotations'][0]['result'][0]['value']['choices'] = ['수정']
    parent['annotations'][0]['result'].append({'from_name':'corrected','to_name':'answer','value':{'text':['교육과정 개선을 추진합니다.']}})
    assert convert([parent,child])[1]['issues'] == {'history_changed':1}


def test_config_upgrade_idempotent_and_does_not_require_acceptance():
    upgraded = upgrade_config('<View><Text name="answer" value="$answer"/></View>')
    assert upgrade_config(upgraded) == upgraded
    for key in CHECK_LABELS: assert 'name="'+key+'"' in upgraded
    assert 'required=' not in upgraded


@pytest.mark.parametrize('answer,code', [('연락처: 010-0000-0000', 'privacy_requires_revision'),
                                       ('교과목 99개를 개발합니다.', 'unsupported_number')])
def test_private_or_unsupported_corrected_answer_held(answer, code):
    t = task()
    t['annotations'][0]['result'][0]['value']['choices'] = ['수정']
    t['annotations'][0]['result'].append({'from_name':'corrected','to_name':'answer','value':{'text':[answer]}})
    pairs, report = convert([t])
    assert not pairs and report['issues'] == {code:1}


def test_duplicate_cycle_and_multiple_reviews_are_not_approved():
    assert convert([task(), task()])[1]['approved'] == 0
    t = task(); t['data']['_record']['parent'] = 'one'
    assert convert([t])[1]['issues'] == {'duplicate_or_cyclic_sample':1}
    t = task(); t['annotations'] *= 2
    assert convert([t])[1]['approved'] == 0


def test_only_current_snapshot_is_exported_and_revocation_blocks_old(tmp_path):
    pairs, _ = convert([task()])
    path = tmp_path/'grounded-reviewed-test.jsonl'
    path.write_text(json.dumps(pairs[0], ensure_ascii=False)+'\n')
    with pytest.raises(ValueError): approved_bytes(path)
    (tmp_path/'grounded-current.json').write_text(json.dumps({'filename':path.name}))
    assert approved_bytes(path)
    (tmp_path/'grounded-current.json').write_text(json.dumps({'filename':''}))
    with pytest.raises(ValueError): approved_bytes(path)


def test_import_route_idempotent_and_revoked_snapshot_not_downloadable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login
    from zzaimy.dataset import ls_client, build
    app = _app(tmp_path)
    c = TestClient(app)
    assert _login(c, 'zzdev', 'devpass')
    db = app.state.db
    db.set_setting('labelstudio_url', 'http://example.invalid')
    db.set_setting('labelstudio_token', 'test-token')
    monkeypatch.setattr(build, 'SFT_DIR', tmp_path/'sft')
    monkeypatch.setattr(ls_client.LabelStudioClient, 'status', lambda *a: {'ok':True,'project_id':5})
    rows = [task()]
    monkeypatch.setattr(ls_client.LabelStudioClient, 'export_tasks', lambda *a: deepcopy(rows))
    monkeypatch.setattr(db, 'get_document', lambda *a: {'id':1})
    monkeypatch.setattr(db, 'list_doc_chunks', lambda *a: [{'id':2,'kind':'text','content':'교육과정을 개선합니다.'}])
    for _ in range(2):
        response = c.post('/dev/data/grounded-pull', follow_redirects=False)
        assert 'ok=' in response.headers['location']
    assert db.count_datasets() == 1
    ds = db.list_datasets()[0]
    assert c.get(f"/dev/data/{ds['id']}.jsonl").status_code == 200
    rows[0]['annotations'] = []
    assert 'ok=' in c.post('/dev/data/grounded-pull', follow_redirects=False).headers['location']
    assert c.get(f"/dev/data/{ds['id']}.jsonl").status_code == 409
    report = json.loads(db.get_setting('grounded_review_report'))
    assert report['approved'] == 0 and report['held'] == 1


def test_export_includes_unreviewed_tasks(monkeypatch):
    from zzaimy.dataset.ls_client import LabelStudioClient
    client = LabelStudioClient('http://example.invalid', 'test-token')
    rows = [{'id': 1, 'annotations': []}]
    calls = []
    def request(method, path):
        calls.append((method, path))
        return rows
    monkeypatch.setattr(client, '_req', request)
    assert client.export_tasks(5) == rows
    assert calls == [('GET', '/api/projects/5/export?exportType=JSON&download_all_tasks=true')]


def test_staff_cannot_import_or_change_review_config(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login
    c = TestClient(_app(tmp_path))
    assert _login(c, 'zzaimy', 'boot-pass-1')
    for path in ('grounded-pull', 'grounded-review-config'):
        assert c.post('/dev/data/'+path).status_code == 403
