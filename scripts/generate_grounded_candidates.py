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
from zzaimy.dataset.batch_candidates import SYSTEM, windows, select_documents, job_key, convert_response
from zzaimy.dataset.authoring import prepare_tasks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--retry', action='store_true')
    ap.add_argument('--per-program', type=int, default=0, help='0: all eligible documents')
    ap.add_argument('--max-jobs', type=int, default=20)
    ap.add_argument('--windows-per-document', type=int, default=3)
    ap.add_argument('--delay', type=float, default=2)
    ap.add_argument('--output', type=Path, default=Path('data/training/generated-candidates'))
    args = ap.parse_args()
    if args.per_program < 0 or args.max_jobs < 1 or args.windows_per_document < 1 or args.delay < 0:
        ap.error('positive limits required')
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
            context = {'program_id': program['program'], 'program': program['program_name'],
                       'doc_id': did, 'filename': doc['filename'], 'light': bool(entry.get('light'))}
            header = '\n'.join(c['text'] for w in windows(chunks[:5]) for c in w)[:3000]
            document_attempts = 0
            for window in windows(chunks):
                key = job_key({**context, 'header': header}, window)
                target = args.output / (key + '.json')
                if target.exists():
                    previous = json.loads(target.read_text())
                    if not args.retry or previous['status'] != 'error':
                        counts['skipped_existing_windows'] += 1
                        continue
                if attempted >= args.max_jobs:
                    print(json.dumps(dict(counts)), flush=True)
                    return
                if document_attempts >= args.windows_per_document:
                    break
                attempted += 1
                document_attempts += 1
                result = {'job': key, 'context': context, 'model': model, 'status': 'error',
                          'source_window': window, 'source_header': header, 'approved': False}
                try:
                    response = client.chat.completions.create(model=model, temperature=0.2, max_tokens=4500,
                        messages=[{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps(
                            {'context':context,'header':header,'chunks':window}, ensure_ascii=False)}],
                        extra_body={'chat_template_kwargs': {'enable_thinking': False}})
                    choice = response.choices[0]
                    if choice.finish_reason == 'length':
                        raise ValueError('truncated_output')
                    content = choice.message.content or ''
                    if content.strip().startswith('```'):
                        content = content.strip().split('\n',1)[1].rsplit('```',1)[0]
                    result['raw_response'] = content
                    rows = convert_response(json.loads(content), context, window, key)
                    def resolve(docid, cid):
                        # Re-read current sources: reprocessing during generation must invalidate the window.
                        current = {c['id']: c['text'] for w in windows(db.list_doc_chunks(docid)) for c in w}
                        original = next((c['text'] for c in window if c['id']==cid), None)
                        if current.get(cid) != original:
                            return None
                        return {'text':original,'location':f"{doc['filename']} · 조각 {cid}"}
                    manifest = {'program_id':context['program_id'], 'program':context['program'], 'document_ids':[did]}
                    tasks, report = prepare_tasks(rows, manifest, resolve, author=f'local-model:{model}')
                    result.update(rows=rows, manifest=manifest, tasks=tasks, preflight=report,
                                  status='held' if report['held'] else 'candidate')
                    counts['generated_candidates'] += len(rows)
                    counts['held_candidates'] += report['held']
                except Exception as exc:
                    # Exception class only: model/HTTP exception details may contain sensitive inputs.
                    result['error_type'] = type(exc).__name__
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


if __name__ == '__main__':
    main()
