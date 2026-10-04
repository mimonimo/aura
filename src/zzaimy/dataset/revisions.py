"""Revise unannotated leaf candidates without inheriting stale review claims."""
from copy import deepcopy
from zzaimy.dataset.authoring import content_digest
from zzaimy.dataset.question_context import question_issues


def revise_task(task, proposal, all_tasks, resolve_source):
    sid = task['data']['sample_id']
    if any(t['data'].get('_record', {}).get('parent') == sid for t in all_tasks):
        raise ValueError('dependent_turns_require_joint_revision')
    record = task['data']['_record']
    if record.get('parent') or record.get('history'):
        raise ValueError('standalone_current_candidate_required')
    return _revise(task, proposal, resolve_source, [])


def _revise(task, proposal, resolve_source, history):
    if task.get('annotations'):
        raise ValueError('annotated_task_requires_review_migration')
    data = deepcopy(task['data'])
    record = data['_record']
    if data.get('superseded_by'):
        raise ValueError('standalone_current_candidate_required')
    if any(not isinstance(proposal.get(k), str) or not proposal[k].strip()
           for k in ('question', 'answer', 'rationale')):
        raise ValueError('revision_content_required')
    for ref, expected in zip(record['refs'], record['source_texts'], strict=True):
        if resolve_source(*ref) != expected:
            raise ValueError('source_changed')
    if question_issues(proposal['question'], data['program'], parent=bool(history)):
        raise ValueError('question_context_incomplete')
    for key in ('question', 'answer', 'rationale'):
        data[key] = record[key] = proposal[key]
    record['history'] = history
    data['history'] = '\n\n'.join('질문: '+t['question']+'\n답변: '+t['answer'] for t in history) or '단독 질문'
    record['revision'] = int(record.get('revision', 1)) + 1
    record['reviewed'] = False
    data.pop('ai_review', None)
    data['ai_review_summary'] = '내용 보강 · 이전 AI 검수 무효 · 독립 검수 대기'
    data['preflight'] = {'content_sha256': content_digest(data), 'semantic_reviewed': False,
                         'issues': [], 'outline_checked': False}
    data.update({'관리_상태': '원문 대조 대기', '관리_승인': 'SFT 승인 별도',
                 '관리_사유': '', '관리_버전': record['revision']})
    return data


def revise_batch(tasks, proposals, resolve_source):
    """Require the complete root-to-descendants group; rebuild actual prior turns."""
    by_id = {t['data']['sample_id']: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError('duplicate_sample_id')
    if not proposals or not set(proposals) <= by_id.keys():
        raise ValueError('unknown_or_empty_proposals')
    for sid, task in by_id.items():
        parent = task['data']['_record'].get('parent')
        if parent in proposals and sid not in proposals:
            raise ValueError('dependent_turns_require_joint_revision')
        if sid in proposals and parent and parent not in proposals:
            raise ValueError('ancestor_requires_joint_revision')
    result = {}
    while len(result) < len(proposals):
        advanced = False
        for sid, proposal in proposals.items():
            if sid in result:
                continue
            task = by_id[sid]
            parent = task['data']['_record'].get('parent')
            if parent and parent not in result:
                continue
            history = []
            if parent:
                previous = result[parent]
                if previous['program'] != task['data']['program'] or previous.get('program_id') != task['data'].get('program_id'):
                    raise ValueError('cross_program_parent')
                history = deepcopy(previous['_record']['history']) + [
                    {key: previous[key] for key in ('question', 'answer')}]
            result[sid] = _revise(task, proposal, resolve_source, history)
            advanced = True
        if not advanced:
            raise ValueError('cyclic_parent')
    return result
