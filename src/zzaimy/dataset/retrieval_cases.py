"""Offline grounding-selection cases. No inference, network or automatic approval.

The candidates are excerpts from authorized retrieval results. ``source_hash``
binds the exact UTF-8 excerpt (without normalization), not the whole document.
IDs and excerpts must also be checked against an authorized source snapshot by
the calling adapter; a matching hash alone does not establish authenticity.
"""
from __future__ import annotations

import hashlib
import json
import re


def digest(case):
    payload = {k: v for k, v in case.items() if k != 'review'}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_case(case):
    """Structural and scope checks only; reasons and answers still need review."""
    if not isinstance(case, dict):
        raise ValueError('invalid_case')
    if any(not _text(case.get(k)) for k in ('id', 'question', 'resolved_question', 'program_id', 'purpose')):
        raise ValueError('missing_case_context')
    if case.get('decision') not in ('answer', 'clarify', 'retrieve_more'):
        raise ValueError('invalid_decision')
    if case['decision'] != 'answer' and not _text(case.get('next_action')):
        raise ValueError('missing_next_action')
    years = case.get('year_ids', [])
    if not isinstance(years, list) or any(not _text(year) for year in years):
        raise ValueError('invalid_year_scope')
    references = case.get('reference_program_ids', [])
    if not isinstance(references, list) or any(not _text(p) for p in references):
        raise ValueError('invalid_reference_scope')
    candidates = case.get('candidates')
    if not isinstance(candidates, list):
        raise ValueError('invalid_candidates')
    by_id = {}
    for item in candidates:
        if not isinstance(item, dict) or any(not _text(item.get(k)) for k in
                ('node_id', 'program_id', 'document_kind', 'text', 'source_hash')):
            raise ValueError('missing_candidate_provenance')
        if not re.fullmatch(r'doc:[1-9]\d*:sec:[1-9]\d*(?:\.[1-9]\d*)*', item['node_id']):
            raise ValueError('invalid_section_id')
        if not re.fullmatch(r'[a-f0-9]{64}', item['source_hash']):
            raise ValueError('invalid_source_hash')
        if hashlib.sha256(item['text'].encode('utf-8')).hexdigest() != item['source_hash']:
            raise ValueError('source_excerpt_hash_mismatch')
        if item['node_id'] in by_id:
            raise ValueError('duplicate_candidate')
        by_id[item['node_id']] = item
    selected, rejected = case.get('selected'), case.get('rejected')
    if not isinstance(selected, list) or not isinstance(rejected, list):
        raise ValueError('missing_selection')
    seen = set()
    for chosen, entries in ((True, selected), (False, rejected)):
        for entry in entries:
            if not isinstance(entry, dict) or not _text(entry.get('reason')):
                raise ValueError('missing_selection_reason')
            node_id = entry.get('node_id')
            if not _text(node_id) or node_id not in by_id or node_id in seen:
                raise ValueError('unknown_or_repeated_selection')
            seen.add(node_id)
            if not chosen:
                continue
            item = by_id[node_id]
            role = entry.get('use_as')
            if role not in ('applicable_criterion', 'target_evidence', 'reference_example'):
                raise ValueError('invalid_evidence_role')
            if role == 'reference_example':
                if item['program_id'] not in {case['program_id'], *references}:
                    raise ValueError('undeclared_cross_program_reference')
            elif item['program_id'] != case['program_id']:
                raise ValueError('cross_program_criterion_or_target')
            if role != 'reference_example' and years and item.get('year_id') not in years:
                raise ValueError('year_scope_mismatch')
    if seen != set(by_id):
        raise ValueError('unaccounted_candidate')
    if case['decision'] == 'answer' and not selected:
        raise ValueError('answer_without_selected_evidence')
    return case


def evaluation_row(case):
    """Export only independently reviewed, snapshot-bound answerable gold cases."""
    validate_case(case)
    review = case.get('review') or {}
    if (not isinstance(review, dict)
            or review.get('decision') != 'accept' or not _text(review.get('reviewer'))
            or review.get('independent') is not True or review.get('source_checked') is not True
            or review.get('content_sha256') != digest(case)):
        raise ValueError('independent_source_review_required')
    if case['decision'] != 'answer':
        raise ValueError('non_answerable_case_not_retrieval_gold')
    # Do not expose selection rationales or expected nodes in the query itself.
    return {'id': case['id'], 'question': case['resolved_question'],
            'original_question': case['question'], 'program_id': case['program_id'],
            'gold_nodes': [s['node_id'] for s in case['selected']],
            'content_sha256': digest(case), 'reviewer': review['reviewer']}
