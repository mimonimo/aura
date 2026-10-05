"""Agent review receipts bound to the exact generated candidate, not SFT approval."""
import hashlib
import json
from pathlib import Path


def candidate_digest(candidate):
    return hashlib.sha256(json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def review_receipt(candidate, findings, *, reviewer, reviewed_at):
    if findings.get('decision') not in {'rework_required', 'source_check_required', 'checked_candidate'}:
        raise ValueError('invalid_review_decision')
    if not reviewer or not isinstance(findings.get('issues'), list):
        raise ValueError('invalid_review')
    if findings['decision'] != 'checked_candidate' and not findings['issues']:
        raise ValueError('review_reason_required')
    sample_ids = {row['id'] for row in candidate.get('rows', [])}
    for issue in findings['issues']:
        if issue.get('sample_id') not in sample_ids or not issue.get('reason') or not issue.get('action'):
            raise ValueError('invalid_review_issue')
    return dict(findings, job=candidate['job'], candidate_sha256=candidate_digest(candidate),
                reviewer=reviewer, reviewer_type='agent', reviewed_at=reviewed_at, approved=False)


def review_matches(candidate, receipt):
    return (receipt.get('job') == candidate.get('job')
            and receipt.get('candidate_sha256') == candidate_digest(candidate))


def save_review(directory, candidate, findings, *, reviewer, reviewed_at):
    """Keep append-only receipts; never modify a generated candidate."""
    receipt = review_receipt(candidate, findings, reviewer=reviewer, reviewed_at=reviewed_at)
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(payload.encode()).hexdigest() + '.json')
    try:
        with path.open('x') as stream:
            stream.write(payload + '\n')
    except FileExistsError:
        if json.loads(path.read_text()) != receipt:
            raise ValueError('review_receipt_conflict')
    return path
