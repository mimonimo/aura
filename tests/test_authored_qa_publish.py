"""직접 작성 후보의 게시 전 계약. 네트워크 호출 없이 검증한다."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('authored_publish', Path(__file__).parents[1] / 'scripts/publish_authored_qa.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def row(**changes):
    return dict(dict(id='one', kind='조건 확인', question='가능한가?', answer='조건 확인 필요', rationale='주석에 조건 명시', path=['사업', '조건'], refs=[[1, 2]]), **changes)


def test_followup_retains_parent():
    publisher.validate_rows([row(), row(id='two', parent='one', question='예외는?')])


@pytest.mark.parametrize('rows', [
    [], [row(), row()], [row(answer=' ')], [row(refs=[])],
    [row(refs=[[True, 2]])], [row(path=[])], [row(parent='missing')],
    [row(parent='one')], [row(parent='two'), row(id='two', parent='one', question='다음?')],
    [row(), row(id='two', question=' 가능한가?  ')],
])
def test_rejects_invalid_candidates(rows):
    with pytest.raises(ValueError):
        publisher.validate_rows(rows)


def test_remote_adapter_compiles_and_uses_shared_preflight():
    compile(publisher.REMOTE, '<remote-publisher>', 'exec')
    assert 'prepare_tasks(' in publisher.REMOTE
    assert "payload['manifest']" in publisher.REMOTE
    assert "payload.get('dry_run')" in publisher.REMOTE
    assert '2026학년도 AID' not in publisher.REMOTE
