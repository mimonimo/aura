"""Program-independent QA preparation. No DB, network or approval side effects.

A manifest is an explicitly selected document set, not an inferred classification.
Optional node_paths must come from a reviewed document outline. Missing outlines
are reported as unverified, never as full coverage.
"""
from copy import deepcopy
import hashlib
import json
import unicodedata
from zzaimy.dataset.question_context import question_issues


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('empty_candidates')
    by_id, questions = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_candidate')
        if any(not _text(row.get(k)) for k in ('id', 'kind', 'question', 'answer', 'rationale')):
            raise ValueError('missing_candidate_field')
        if row['id'] in by_id:
            raise ValueError('duplicate_sample_id')
        by_id[row['id']] = row
        if not isinstance(row.get('path'), list) or not row['path'] or any(not _text(p) for p in row['path']):
            raise ValueError('missing_node_path')
        refs = row.get('refs')
        if (not isinstance(refs, list) or not refs or any(not isinstance(ref, list)
                or len(ref) != 2 or any(type(v) is not int or v <= 0 for v in ref) for ref in refs)):
            raise ValueError('invalid_source_reference')
        if len({tuple(ref) for ref in refs}) != len(refs):
            raise ValueError('duplicate_source_reference')
        if row.get('parent') is not None and not _text(row['parent']):
            raise ValueError('invalid_parent')
        if 'revision_of' in row and (not _text(row['revision_of']) or row['revision_of'] == row['id']):
            raise ValueError('invalid_revision')
        question = ' '.join(unicodedata.normalize('NFKC', row['question']).split())
        key = (row.get('parent'), question)
        if key in questions:
            raise ValueError('duplicate_question_in_context')
        questions.add(key)
    for row in rows:
        seen, parent = {row['id']}, row.get('parent')
        while parent is not None:
            if parent not in by_id or parent in seen:
                raise ValueError('missing_or_cyclic_parent')
            seen.add(parent)
            parent = by_id[parent].get('parent')


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or any(not _text(manifest.get(k)) for k in ('program_id', 'program')):
        raise ValueError('explicit_program_required')
    aliases = manifest.get('program_aliases', [])
    if not isinstance(aliases, list) or any(not _text(alias) for alias in aliases):
        raise ValueError('invalid_program_aliases')
    ids = manifest.get('document_ids')
    if (not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('invalid_document_scope')
    paths = manifest.get('node_paths')
    if paths is not None:
        if (not isinstance(paths, list) or not paths or any(not isinstance(p, list) or not p
                or any(not _text(s) for s in p) for p in paths)):
            raise ValueError('invalid_outline')
        if len({tuple(p) for p in paths}) != len(paths):
            raise ValueError('duplicate_outline_path')


def content_digest(data):
    fields = {k: data[k] for k in ('question', 'answer', 'evidence', 'history', 'path')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prepare_tasks(rows, manifest, resolve_source, *, author, privacy_check=None, number_check=None):
    """Return candidate tasks + diagnostics, without granting semantic approval.

    resolve_source(doc_id, chunk_id) -> {'text': str, 'location': str} or None.
    Caller must resolve only authorized documents; membership in this manifest
    does not replace access checks. Checks are injectable for offline tests.
    Structural/source identity failures abort the batch before any publication.
    Content issues hold the affected candidate and all dependent followups.
    """
    validate_rows(rows)
    validate_manifest(manifest)
    if not _text(author):
        raise ValueError('author_required')
    if privacy_check is None:
        from zzaimy.dataset.privacy import protect_candidate
        privacy_check = lambda value: protect_candidate(value) == value
    if number_check is None:
        from zzaimy.verify.numbers import verify_numbers
        number_check = lambda answer, sources: verify_numbers(answer, sources).ok
    program_id, program = manifest['program_id'].strip(), manifest['program'].strip()
    allowed = set(manifest['document_ids'])
    paths = {tuple(p) for p in manifest['node_paths']} if manifest.get('node_paths') else None
    by_id = {r['id']: r for r in rows}
    cache, tasks, issues = {}, [], {}
    referenced_docs, used_paths = set(), set()
    def source_for(did, cid):
        if did not in allowed:
            raise ValueError('source_outside_manifest')
        key = (did, cid)
        if key not in cache:
            source = resolve_source(did, cid)
            if (not isinstance(source, dict) or not _text(source.get('text'))
                    or not _text(source.get('location'))):
                raise ValueError('source_missing_or_empty')
            cache[key] = dict(source)
        return cache[key]
    for row in rows:
        if row.get('program_id', program_id) != program_id:
            raise ValueError('cross_program_candidate')
        if any(ref[0] not in allowed for ref in row['refs']):
            raise ValueError('source_outside_manifest')
        if paths is not None and tuple(row['path']) not in paths:
            raise ValueError('node_path_outside_outline')
        evidence, locations = [], []
        for did, cid in row['refs']:
            source = source_for(did, cid)
            evidence.append(source['text'])
            locations.append(source['location'])
            referenced_docs.add(did)
        chain, parent = [], row.get('parent')
        number_sources = list(evidence)
        while parent:
            previous = by_id[parent]
            chain.insert(0, {'question': previous['question'], 'answer': previous['answer']})
            number_sources.extend(source_for(*ref)['text'] for ref in previous['refs'])
            parent = previous.get('parent')
        data = dict(sample_id=row['id'], program_id=program_id, program=program,
                    path=' → '.join(row['path']), kind=row['kind'], question=row['question'],
                    answer=row['answer'], rationale=row['rationale'], source='\n'.join(locations),
                    evidence='\n\n'.join(evidence),
                    history='\n\n'.join('질문: '+p['question']+'\n답변: '+p['answer'] for p in chain) or '단독 질문',
                    ai_review_summary='AI 검수 미진행 · 사람 승인과 별도')
        data['_record'] = dict(deepcopy(row), program_id=program_id, history=chain,
                               source_texts=evidence, author=author, reviewed=False)
        found = question_issues(row['question'], program, parent=bool(row.get('parent')),
                                aliases=manifest.get('program_aliases', []))
        if not privacy_check(data):
            found.append('privacy_requires_revision')
        if not number_check(row['answer'], number_sources):
            found.append('unsupported_number')
        issues[row['id']] = found
        data['preflight'] = {'content_sha256': content_digest(data), 'issues': found,
                             'outline_checked': paths is not None, 'semantic_reviewed': False}
        tasks.append({'data': data})
        used_paths.add(tuple(row['path']))
    # Propagate holds regardless of input order. No reviewed-looking orphan turns.
    for row in rows:
        parent = row.get('parent')
        while parent:
            if issues[parent]:
                issues[row['id']].append('ancestor_held')
                break
            parent = by_id[parent].get('parent')
    return tasks, {'total': len(tasks), 'held': sum(bool(v) for v in issues.values()),
                   'issues': {k: v for k, v in issues.items() if v},
                   'documents_without_candidates': sorted(allowed - referenced_docs),
                   'outline_match_checked': paths is not None,
                   'nodes_without_candidates': [list(p) for p in sorted(paths - used_paths)] if paths else None,
                   'semantic_review_required': True}
