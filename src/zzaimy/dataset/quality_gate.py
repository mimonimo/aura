"""모델 독립 SFT 후보의 보수적 품질 관문. 통과는 의미 정확성의 자동 증명이 아니다.

원문/대화 내용을 로그에 싣지 않으며 입력을 수정하지 않는다.
검수자는 원문을 확인한 뒤 review.checks를 기록해야 한다.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from zzaimy.verify.numbers import verify_numbers

REVIEW_CHECKS = ('grounding', 'structure', 'context', 'privacy')


def audit_pair(pair: dict) -> list[str]:
    issues = set()
    meta = pair.get('meta') or {}
    program = meta.get('program_id')
    path = meta.get('node_path')
    if not isinstance(program, str) or not program.strip():
        issues.add('missing_program_id')
    if not isinstance(path, list) or not path or any(not isinstance(p, str) or not p.strip() for p in path):
        issues.add('missing_node_path')
    review = meta.get('review') or {}
    if (review.get('decision') not in ('accept', 'corrected') or not review.get('reviewer')
            or any((review.get('checks') or {}).get(k) is not True for k in REVIEW_CHECKS)):
        issues.add('review_incomplete')
    turns = pair.get('conversations')
    if not isinstance(turns, list) or not turns or len(turns) % 2:
        return sorted(issues | {'invalid_conversation'})
    evidence = meta.get('evidence_records')
    if not isinstance(evidence, list) or not evidence:
        evidence = []
        issues.add('missing_evidence_records')
    valid = []
    for e in evidence:
        if not isinstance(e, dict):
            issues.add('invalid_evidence_record')
            continue
        turn = e.get('turn')
        if (not e.get('doc_id') or not e.get('chunk_id') or not isinstance(e.get('text'), str)
                or not e['text'].strip() or type(turn) is not int or turn < 0 or turn >= len(turns) or turn % 2):
            issues.add('invalid_evidence_record')
            continue
        if not program or e.get('program_id') != program:
            issues.add('cross_program_evidence')
            continue
        # A provenance record alone is insufficient: the model must actually see it.
        if e['text'] not in str(turns[turn].get('value', '')):
            issues.add('evidence_not_visible')
            continue
        valid.append(e)
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict) or turn.get('from') != ('human' if i % 2 == 0 else 'gpt'):
            issues.add('invalid_turn_order')
            continue
        text = turn.get('value')
        if not isinstance(text, str) or not text.strip():
            issues.add('empty_turn')
            continue
        if i % 2:
            sources = [e['text'] for e in valid if e['turn'] < i]
            if not sources:
                issues.add('answer_without_evidence')
            # Only explicit formatting markers are removed, never all short numbers.
            body = re.sub(r'\[\d+단계:[^\]]*\]|\[E\d+\]', '', text)
            if not verify_numbers(body, sources).ok:
                issues.add('unsupported_number')
    return sorted(issues)


def audit_dataset(pairs: list[dict]) -> dict:
    """검수 완료 후보와 보류 건수를 분리한다. 동일 사업은 한 split에만 허용."""
    groups = {}
    for pair in pairs:
        meta = pair.get('meta') or {}
        if meta.get('program_id') and meta.get('split'):
            groups.setdefault(meta['program_id'], set()).add(meta['split'])
    seen = set()
    rows = []
    for index, pair in enumerate(pairs, 1):
        issues = audit_pair(pair)
        meta = pair.get('meta') or {}
        if len(groups.get(meta.get('program_id'), set())) > 1:
            issues.append('program_split_leakage')
        digest = hashlib.sha256(json.dumps(pair.get('conversations'), ensure_ascii=False,
                                         sort_keys=True).encode()).hexdigest()
        if digest in seen:
            issues.append('duplicate_conversation')
        seen.add(digest)
        rows.append({'row': index, 'issues': sorted(set(issues))})
    accepted = sum(not row['issues'] for row in rows)
    return {'total': len(rows), 'eligible': accepted, 'held': len(rows) - accepted,
            'issues': dict(Counter(code for row in rows for code in row['issues'])), 'rows': rows}
