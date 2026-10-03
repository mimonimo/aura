from zzaimy.dataset.workspace import plan_tasks


def task(n, sid, **fields):
    return {'id': n, 'annotations': [], 'data': {'sample_id': sid, 'program': '사업A',
            'question': '사업A 목적은?', '_record': {}, **fields}}


def test_only_explicit_replaced_leaves_removed():
    old, new = task(1, 'old', superseded_by='new'), task(2, 'new')
    updates, deleted = plan_tasks([old, new])
    assert deleted == [1] and len(updates) == 1
    child = task(3, 'child', _record={'parent': 'old'})
    assert plan_tasks([old, new, child])[1] == []
    old['annotations'] = [{'id': 10}]
    assert plan_tasks([old, new])[1] == []


def test_missing_or_cross_program_successor_preserved():
    old = task(1, 'old', superseded_by='new')
    assert plan_tasks([old])[1] == []
    assert plan_tasks([old, task(2, 'new', program='사업B')])[1] == []


def test_context_flag_does_not_change_content_or_approve():
    item = task(1, 'a', question='금액은 얼마야?')
    updates, deleted = plan_tasks([item])
    assert not deleted
    assert updates[0]['data']['관리_상태'] == '맥락 보강 필요'
    assert updates[0]['data']['관리_승인'] == 'SFT 승인 별도'
    assert '관리_상태' not in item['data']
