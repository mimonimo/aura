"""Read-only inventory of generated candidates; never infer SFT approval."""
from collections import Counter, defaultdict
import hashlib
import json


def summarize(results):
    latest, historical = {}, 0
    for result in results:
        context = result['context']
        source = json.dumps([context['program_id'], context['doc_id'], result['source_window']],
                            sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(source.encode()).hexdigest()
        historical += 1
        previous = latest.get(key)
        if previous is None or result.get('completed_at', 0) > previous.get('completed_at', 0):
            # Keep counters, not the corpus or model responses, in memory.
            latest[key] = {k:result[k] for k in ('context', 'status', 'completed_at',
                           'semantic_review', 'hold_reason', 'error_type', 'preflight') if k in result}
            latest[key]['turn_count'] = len(result.get('rows', []))
    totals, reasons = Counter(), Counter()
    programs = defaultdict(Counter)
    last_completed = 0
    for result in latest.values():
        state = result['status']
        if state not in {'candidate', 'held', 'error'}:
            state = 'unknown'
        turns = result['turn_count']
        for counts in (totals, programs[result['context']['program_id']]):
            counts['windows'] += 1
            counts[state + '_windows'] += 1
            counts['generated_turns'] += turns
            counts[state + '_turns'] += turns
            counts['conversations'] += bool(turns)
            if state == 'candidate' and not result.get('semantic_review', {}).get('passed'):
                counts['semantic_pending_windows'] += 1
        if result.get('hold_reason'):
            reasons[result['hold_reason']] += 1
        if state == 'error':
            reasons['error:' + result.get('error_type', 'unknown')] += 1
        for issues in result.get('preflight', {}).get('issues', {}).values():
            reasons.update(issues)
        last_completed = max(last_completed, result.get('completed_at', 0))
    return {'historical_windows': historical, 'superseded_same_source_windows': historical - len(latest),
            'totals':dict(totals), 'by_program':{k:dict(v) for k,v in sorted(programs.items())},
            'issues':dict(reasons), 'last_completed_at':last_completed,
            'source_freshness_verified':False, 'semantic_duplicates_checked':False,
            'sft_approval_checked':False}
