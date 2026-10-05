"""Resumable internal-model candidate generation. Does not approve or train.

Run from repository root. Default lists the bounded plan; --apply generates.
Each source-version window has a durable result. Errors retry only with --retry.
"""
import argparse
from collections import Counter
import fcntl
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path.cwd() / 'src'))
from zzaimy.dataset.batch_candidates import SYSTEM, REVIEW_SYSTEM, semantic_result, windows, select_documents, job_key, convert_response, generate_checked
from zzaimy.dataset.authoring import prepare_tasks
from zzaimy.dataset.privacy import protect_candidate
from zzaimy.verify.numbers import verify_numbers


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--retry', action='store_true')
    ap.add_argument('--per-program', type=int, default=0, help='0: all eligible documents')
    ap.add_argument('--max-jobs', type=int, default=20)
    ap.add_argument('--windows-per-document', type=int, default=3)
    ap.add_argument('--delay', type=float, default=2)
    ap.add_argument('--continuous', action='store_true', help='repeat batches until no new source windows remain')
    ap.add_argument('--defer-semantic-review', action='store_true', help='save preflight candidates for a separate semantic review pass')
    ap.add_argument('--output', type=Path, default=Path('data/training/generated-candidates'))
    args = ap.parse_args()
    if args.per_program < 0 or args.max_jobs < 1 or args.windows_per_document < 1 or args.delay < 0:
        ap.error('positive limits required')
    while True:
        attempted = run_batch(args)
        if not args.continuous or not attempted:
            return
        time.sleep(10)


def run_batch(args):
    from zzaimy.app.db import Database
    db = Database(Path('data/platform/platform.db'))
    catalog = json.loads(Path('data/platform/program_core_docs.json').read_text())
    selected = list(select_documents(catalog, args.per_program))
    print(json.dumps({'selected_documents': len(selected), 'programs': len({p['program'] for p,d in selected}),
                      'max_new_windows': args.max_jobs, 'apply': args.apply}), flush=True)
    if not args.apply:
        return
    from zzaimy.generate import model_config
    from openai import OpenAI
    cfg = model_config.current('review')
    if not cfg['configured'] or cfg.get('external') or cfg.get('kind') != 'vllm':
        raise RuntimeError('configured_internal_model_required')
    client = OpenAI(base_url=cfg['base_url'], api_key=cfg['api_key'], timeout=150, max_retries=0)
    model = cfg['model'] or client.models.list().data[0].id
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.output / '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        counts, attempted = Counter(), 0
        for program, entry in selected:
            did = entry['doc_id']
            doc = db.get_document(did)
            if not doc:
                continue
            chunks = db.list_doc_chunks(did)
            # Protect only the training copy before the model sees it. Originals remain unchanged.
            original_chunks = chunks
            chunks = protect_candidate(chunks)
            context = {'program_id': program['program'], 'program': program['program_name'],
                       'doc_id': did, 'filename': doc['filename'], 'light': bool(entry.get('light'))}
            header_chunks = []
            for w in windows(chunks[:5]):
                for c in w:
                    if sum(len(x['text']) for x in header_chunks) + len(c['text']) <= 3000:
                        header_chunks.append(c)
            header = '\n'.join(c['text'] for c in header_chunks)
            document_attempts = 0
            for window in windows(chunks):
                # Title/year chunks stay citable in later turns; header is not an uncited fact source.
                window = list({c['id']: c for c in [*header_chunks, *window]}.values())
                key = job_key({**context, 'header': header}, window)
                target = args.output / (key + '.json')
                if target.exists():
                    previous = json.loads(target.read_text())
                    if not args.retry or previous['status'] != 'error':
                        counts['skipped_existing_windows'] += 1
                        continue
                if attempted >= args.max_jobs:
                    print(json.dumps(dict(counts)), flush=True)
                    return attempted
                if document_attempts >= args.windows_per_document:
                    break
                attempted += 1
                document_attempts += 1
                result = {'job': key, 'context': context, 'model': model, 'status': 'error',
                          'source_window': window, 'source_header': header, 'approved': False}
                try:
                    messages = [{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps(
                        {'context':context,'header':header,'chunks':window}, ensure_ascii=False)}]
                    def generate(feedback):
                        if feedback:
                            messages.append({'role':'user','content': '검사 오류를 수정한 전체 대화 JSON을 다시 작성하세요. '
                                '근거를 지어내거나 기준을 낮추지 마세요. 불가능하면 scope_confirmed=false. '
                                + json.dumps(feedback, ensure_ascii=False)})
                        response = client.chat.completions.create(model=model, temperature=0.2, max_tokens=4500,
                            messages=messages, response_format={'type':'json_object'},
                            extra_body={'chat_template_kwargs': {'enable_thinking': False}})
                        choice = response.choices[0]
                        if choice.finish_reason == 'length':
                            raise ValueError('truncated_output')
                        content = choice.message.content or ''
                        messages.append({'role':'assistant','content':content})
                        return content
                    def check(parsed):
                        rows = convert_response(parsed, context, window, key)
                        fresh = db.list_doc_chunks(did)
                        # Compare raw snapshots too: masking must not hide a changed original.
                        if fresh != original_chunks:
                            raise ValueError('source_missing_or_empty')
                        current = {c['id']: c['text'] for w in windows(protect_candidate(fresh)) for c in w}
                        raw_sources = {c['id']: c['text'] for w in windows(fresh) for c in w}
                        def resolve(docid, cid):
                            return {'text':raw_sources.get(cid), 'location':f"{doc['filename']} · 조각 {cid}"}
                        manifest = {'program_id':context['program_id'], 'program':context['program'],
                                    'document_ids':[did], 'protect_sources':True}
                        tasks, report = prepare_tasks(rows, manifest, resolve, author=f'local-model:{model}')
                        evidence, details = [], {}
                        for row in rows:
                            evidence.extend(current[cid] for _, cid in row['refs'])
                            audit = verify_numbers(row['answer'], evidence)
                            if not audit.ok:
                                details[row['id']] = audit.violations
                        return dict(rows=rows, manifest=manifest, tasks=tasks, preflight=report, number_details=details)
                    result.update(generate_checked(generate, check))
                    if result['status'] == 'candidate' and args.defer_semantic_review:
                        result['semantic_review'] = {'status':'pending', 'human_approved':False}
                    if result['status'] == 'candidate' and not args.defer_semantic_review:
                        review = client.chat.completions.create(model=model, temperature=0, max_tokens=1800,
                            response_format={'type':'json_object'},
                            messages=[{'role':'system','content':REVIEW_SYSTEM},
                                      {'role':'user','content':json.dumps({'context':context,
                                        'chunks':window,'dialogue':result['rows']}, ensure_ascii=False)}],
                            extra_body={'chat_template_kwargs': {'enable_thinking': False}})
                        if review.choices[0].finish_reason == 'length':
                            raise ValueError('truncated_semantic_review')
                        result['semantic_response'] = review.choices[0].message.content or ''
                        result['semantic_review'] = semantic_result(result['semantic_response'])
                        if not result['semantic_review']['passed']:
                            result.update(status='held', hold_reason='semantic_review_failed')
                    counts['generated_candidates'] += len(result.get('rows', []))
                    counts['generated_conversations'] += bool(result.get('rows'))
                    counts['held_candidates'] += (len(result.get('rows', []))
                        if result.get('hold_reason') == 'semantic_review_failed'
                        else result.get('preflight', {}).get('held', 0))
                except Exception as exc:
                    # Exception class only: model/HTTP exception details may contain sensitive inputs.
                    result['error_type'] = type(exc).__name__
                    result['status'] = 'error'
                counts[result['status']+'_windows'] += 1
                result['completed_at'] = time.time()
                if target.exists():
                    backup = args.output / (key + f'.retry-{time.time_ns()}.backup')
                    target.rename(backup)
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                temporary.replace(target)
                print(json.dumps({'doc_id':did,'status':result['status'],'counts':dict(counts)}), flush=True)
                time.sleep(args.delay)
        print(json.dumps(dict(counts)), flush=True)
        return attempted


if __name__ == '__main__':
    main()
