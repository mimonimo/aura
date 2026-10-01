from zzaimy.dataset.question_context import question_issues
from tests.test_authoring import prepare, row, manifest


def test_business_prefix_does_not_hide_missing_metric():
    assert question_issues('사업: 합성 사업 / 범위: 성과\n수치는 얼마야?', '합성 사업') == ['missing_metric_in_question']
    assert question_issues('수치는 얼마야?', '합성 사업', parent=True) == ['missing_metric_in_question']


def test_specific_metric_and_explicit_alias_are_allowed():
    assert question_issues('합성 사업 3차년도 실적보고서의 현장실습 참여 인원은?', '합성 사업') == []
    assert question_issues('TEST 사업의 운영 목적은?', '가상 교육 사업', aliases=['TEST']) == []


def test_missing_root_context_holds_descendants_without_rewriting():
    tasks, report = prepare([row(question='이 사업의 목적은?'), row(id='next', parent='one', question='예외 대상은?')])
    assert report['held'] == 2
    assert report['issues']['one'] == ['missing_program_in_standalone_question']
    assert report['issues']['next'] == ['ancestor_held']
    assert tasks[0]['data']['question'] == '이 사업의 목적은?'


def test_alias_manifest_and_missing_task_scope():
    assert prepare([row(question='TEST 사업의 운영 목적은?')], manifest(program_aliases=['TEST']))[1]['held'] == 0
    assert question_issues('사업: 합성 사업\n계획서는 어떻게 작성해야 해?', '합성 사업') == ['missing_task_scope_in_question']
