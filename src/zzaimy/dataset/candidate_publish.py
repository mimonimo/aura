"""Prepare current, protected generated candidates for review, never approval."""
from zzaimy.dataset.authoring import prepare_tasks
from zzaimy.dataset.candidate_review import review_matches


def prepare_publication(candidate, resolve, reviews=()):
    if candidate.get('status') != 'candidate':
        raise ValueError('candidate_not_eligible')
    if not candidate.get('manifest', {}).get('protect_sources'):
        raise ValueError('protected_source_required')
    tasks, report = prepare_tasks(candidate['rows'], candidate['manifest'], resolve,
                                  author='local-model:' + candidate['model'])
    if report['held']:
        raise ValueError('current_preflight_hold')
    original = {t['data']['sample_id']: t['data'] for t in candidate['tasks']}
    matching = [r for r in reviews if review_matches(candidate, r)]
    review = max(matching, key=lambda r: r.get('reviewed_at', ''), default={})
    decision = review.get('decision')
    state = {'rework_required':'재작성 필요', 'source_check_required':'출처 확인 필요',
             'checked_candidate':'에이전트 대조 완료 · 학습 승인 별도'}.get(decision, '원문 대조 대기')
    for task in tasks:
        data = task['data']
        old = original.get(data['sample_id'], {})
        if (not old.get('_record', {}).get('raw_source_sha256') or
                old['_record']['raw_source_sha256'] != data['_record']['raw_source_sha256']):
            raise ValueError('source_changed')
        data.update({'관리_상태':state, '관리_사업':data['program'], '관리_유형':data['kind'],
                     '관리_승인':'SFT 승인 전', '관리_문서절':data['path'],
                     'ai_review_summary':state, 'generator_job':candidate['job']})
    return tasks
