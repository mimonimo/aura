"""Revise unannotated leaf candidates without inheriting stale review claims."""
from copy import deepcopy
from zzaimy.dataset.authoring import content_digest
from zzaimy.dataset.question_context import question_issues


def revise_task(task, proposal, all_tasks, resolve_source):
    if task.get('annotations'):
        raise ValueError('annotated_task_requires_review_migration')
    data = deepcopy(task['data'])
    sid = data['sample_id']
    if any(t['data'].get('_record', {}).get('parent') == sid for t in all_tasks):
        raise ValueError('dependent_turns_require_joint_revision')
    record = data['_record']
    if data.get('superseded_by') or record.get('parent') or record.get('history'):
        raise ValueError('standalone_current_candidate_required')
    if any(not isinstance(proposal.get(k), str) or not proposal[k].strip()
           for k in ('question', 'answer', 'rationale')):
        raise ValueError('revision_content_required')
    for ref, expected in zip(record['refs'], record['source_texts'], strict=True):
        if resolve_source(*ref) != expected:
            raise ValueError('source_changed')
    if question_issues(proposal['question'], data['program']):
        raise ValueError('question_context_incomplete')
    for key in ('question', 'answer', 'rationale'):
        data[key] = record[key] = proposal[key]
    record['revision'] = int(record.get('revision', 1)) + 1
    record['reviewed'] = False
    data.pop('ai_review', None)
    data['ai_review_summary'] = '내용 보강 · 이전 AI 검수 무효 · 독립 검수 대기'
    data['preflight'] = {'content_sha256': content_digest(data), 'semantic_reviewed': False,
                         'issues': [], 'outline_checked': False}
    data.update({'관리_상태': '원문 대조 대기', '관리_승인': 'SFT 승인 별도',
                 '관리_사유': '', '관리_버전': record['revision']})
    return data
