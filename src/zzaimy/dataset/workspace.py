"""Review workspace metadata; never changes answers or reviewer annotations."""
from collections import Counter
from zzaimy.dataset.question_context import question_issues


def plan_tasks(tasks):
    ids = Counter(t['data'].get('sample_id') for t in tasks)
    by_id = {t['data'].get('sample_id'): t for t in tasks}
    referenced = {t['data'].get('_record', {}).get('parent') for t in tasks
                  if not t['data'].get('superseded_by')}
    updates, removals = [], []
    for task in tasks:
        data = task['data']
        sid, successor = data.get('sample_id'), data.get('superseded_by')
        replacement = by_id.get(successor) if successor else None
        # Only an explicitly superseded, unannotated leaf with a present terminal successor.
        if (sid and ids[sid] == 1 and replacement and ids[successor] == 1
                and successor != sid and not replacement['data'].get('superseded_by')
                and not task.get('annotations') and sid not in referenced
                and data.get('program') == replacement['data'].get('program')):
            removals.append(task['id'])
            continue
        record = data.get('_record', {})
        issues = question_issues(data.get('question', ''), data.get('program', ''),
                                 parent=bool(record.get('parent') or record.get('history')))
        if successor:
            status = '구버전 확인 필요'
        elif issues:
            status = '맥락 보강 필요'
        elif task.get('annotations'):
            status = '검수 결과 확인'  # Annotation is not SFT approval.
        else:
            status = '원문 대조 대기'
        fields = {'관리_상태': status, '관리_사업': data.get('program', '미분류'),
                  '관리_유형': data.get('kind', '미분류'),
                  '관리_문서절': data.get('path', ''), '관리_승인': 'SFT 승인 별도',
                  '관리_사유': ', '.join(issues)}
        updates.append({'id': task['id'], 'data': {**data, **fields}})
    return updates, removals
