"""Authored Label Studio tasks -> grounded SFT, without implicit reviewer approval.

Pure conversion except the injected source resolver. Never edits tasks or source documents.
"""
from copy import deepcopy
from collections import Counter
import hashlib
import json
import re
import xml.etree.ElementTree as ET

from zzaimy.dataset.privacy import protect_candidate
from zzaimy.dataset.quality_gate import REVIEW_CHECKS, audit_dataset

PROJECT = 'ZZAIMY 근거 기반 문답 검수'
CHECK_LABELS = {'grounding': '원문 근거 일치', 'structure': '목차·답변 범위',
                'context': '사업·이전 대화 맥락', 'privacy': '개인정보 제거'}


def upgrade_config(config):
    root = ET.fromstring(config)
    if root.tag != 'View' or not any(e.get('name') == 'answer' and e.tag == 'Text' for e in root.iter()):
        raise ValueError('지원하는 문답 검수 설정이 아닙니다.')
    for key, label in CHECK_LABELS.items():
        existing = [e for e in root.iter() if e.get('name') == key]
        if existing:
            if (len(existing) != 1 or existing[0].tag != 'Choices'
                    or existing[0].get('toName') != 'answer'
                    or [e.get('value') for e in existing[0]] != ['확인']):
                raise ValueError('같은 이름의 다른 검수 설정이 있습니다.')
            continue
        ET.SubElement(root, 'Header', value=label, size='4')
        choices = ET.SubElement(root, 'Choices', name=key, toName='answer', choice='multiple')
        ET.SubElement(choices, 'Choice', value='확인')
    return ET.tostring(root, encoding='unicode')


def _annotation(task):
    annotations = task.get('annotations') or []
    # Conflicts, cancelled reviews and predictions never imply acceptance.
    if len(annotations) != 1 or annotations[0].get('was_cancelled'):
        raise ValueError('review_missing_or_ambiguous')
    ann = annotations[0]
    reviewer = ann.get('completed_by')
    if isinstance(reviewer, dict):
        reviewer = reviewer.get('id')
    if not isinstance(reviewer, (str, int)) or not str(reviewer).strip():
        raise ValueError('reviewer_missing')
    fields = {}
    for item in ann.get('result', []):
        name = item.get('from_name')
        if name in fields:
            raise ValueError('ambiguous_review_fields')
        fields[name] = item.get('value', {}) if item.get('to_name') == 'answer' else {}
    decisions = fields.get('decision', {}).get('choices')
    if decisions not in (['채택'], ['수정']):
        raise ValueError('not_accepted')
    if any(fields.get(k, {}).get('choices') != ['확인'] for k in REVIEW_CHECKS):
        raise ValueError('review_incomplete')
    corrected = None
    if decisions == ['수정']:
        values = fields.get('corrected', {}).get('text', [])
        if len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
            raise ValueError('correction_missing')
        corrected = values[0].strip()
    elif any(str(v).strip() for v in fields.get('corrected', {}).get('text', [])):
        raise ValueError('correction_requires_decision')
    return corrected, {'decision': 'corrected' if corrected else 'accept',
                       'reviewer': f'labelstudio:{reviewer}', 'annotation_id': ann.get('id'),
                       'checks': dict.fromkeys(REVIEW_CHECKS, True)}


def convert_tasks(tasks, resolve_source):
    """Return approved pairs and content-free issue counts. All ancestors must pass review.

    resolve_source(doc_id, chunk_id) returns the current source text or None.
    This detects replaced/deleted source chunks; it is not a semantic judge.
    """
    by_id, duplicates = {}, set()
    invalid = 0
    for task in tasks:
        data = task.get('data') if isinstance(task, dict) else None
        sid = data.get('sample_id') if isinstance(data, dict) else None
        if not isinstance(sid, str) or not sid:
            invalid += 1
            continue
        if sid in by_id:
            duplicates.add(sid)
        by_id[sid] = task
    errors, built = {}, {}

    def build(sid, stack=()):
        if sid in stack or sid in duplicates:
            raise ValueError('duplicate_or_cyclic_sample')
        if sid in built:
            return deepcopy(built[sid])
        if sid not in by_id:
            raise ValueError('parent_missing')
        task = by_id[sid]
        data = task['data']
        record = data['_record']
        if data.get('superseded_by'):
            raise ValueError('superseded_sample')
        corrected, review = _annotation(task)
        if (record['id'] != sid or data['question'] != record['question']
                or data['answer'] != record['answer'] or data['path'] != ' → '.join(record['path'])
                or data['evidence'] != '\n\n'.join(record['source_texts'])):
            raise ValueError('display_record_mismatch')
        program = data.get('program')
        if not isinstance(program, str) or not program.strip():
            raise ValueError('missing_program')
        program_id = data.get('program_id')
        if program_id is not None:
            if (not isinstance(program_id, str) or not program_id.strip()
                    or record.get('program_id') != program_id):
                raise ValueError('invalid_program_identity')
        else:
            program_id = 'program:' + hashlib.sha256(program.strip().encode()).hexdigest()[:20]
        turns, evidence, chain = [], [], []
        parent = record.get('parent')
        if parent:
            previous = build(parent, (*stack, sid))
            if previous['meta']['program_id'] != program_id:
                raise ValueError('cross_program_parent')
            # The child reviewer saw this history. A later parent correction invalidates it.
            chain = previous['meta']['reviewed_history']
            if record.get('history') != chain:
                raise ValueError('history_changed')
            turns = previous['conversations']
            evidence = previous['meta']['evidence_records']
        elif record.get('history'):
            raise ValueError('parent_missing')
        shown_history = '\n\n'.join('질문: '+p['question']+'\n답변: '+p['answer'] for p in chain) or '단독 질문'
        if data.get('history') != shown_history:
            raise ValueError('display_history_mismatch')
        refs, texts = record['refs'], record['source_texts']
        if not refs or len(refs) != len(texts):
            raise ValueError('invalid_sources')
        turn = len(turns)
        for index, (ref, text) in enumerate(zip(refs, texts)):
            if (not isinstance(ref, list) or len(ref) != 2 or any(type(v) is not int or v <= 0 for v in ref)
                    or not isinstance(text, str) or not text.strip()):
                raise ValueError('invalid_sources')
            current = resolve_source(*ref)
            if record.get('source_protection') == 'training-copy-v1':
                digests = record.get('raw_source_sha256', [])
                if (not isinstance(current, str) or len(digests) != len(refs)
                        or hashlib.sha256(current.encode()).hexdigest() != digests[index]):
                    raise ValueError('source_changed_or_missing')
                current = protect_candidate(current)
            if current != text:
                raise ValueError('source_changed_or_missing')
            evidence.append(dict(program_id=program_id, doc_id=ref[0], chunk_id=ref[1], text=text, turn=turn))
        answer = corrected or record['answer']
        prompt = f"사업: {program}\n목차: {data['path']}\n[근거]\n{data['evidence']}\n[질문]\n{record['question']}"
        turns += [{'from': 'human', 'value': prompt}, {'from': 'gpt', 'value': answer}]
        pair = {'conversations': turns, 'meta': dict(program_id=program_id, node_path=record['path'],
                sample_id=sid, source='authored_labelstudio', task_id=task.get('id'), review=review,
                evidence_records=evidence, reviewed_history=chain + [dict(question=record['question'], answer=answer)])}
        if protect_candidate(pair) != pair:
            raise ValueError('privacy_requires_revision')
        report = audit_dataset([pair])
        if report['held']:
            raise ValueError(next(iter(report['issues'])))
        built[sid] = deepcopy(pair)
        return pair

    pairs = []
    for sid in by_id:
        try:
            pairs.append(build(sid))
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            code = str(exc)
            errors[sid] = code if isinstance(exc, ValueError) and re.fullmatch('[a-z_]{1,64}', code) else 'invalid_record'
    report = audit_dataset(pairs)
    approved = []
    for pair, row in zip(pairs, report['rows']):
        if row['issues']:
            errors[pair['meta']['sample_id']] = row['issues'][0]
        else:
            approved.append(pair)
    counts = Counter(errors.values())
    if invalid:
        counts['invalid_sample_id'] += invalid
    return approved, {'total': len(tasks), 'approved': len(approved),
                      'held': len(tasks)-len(approved), 'issues': dict(counts)}
