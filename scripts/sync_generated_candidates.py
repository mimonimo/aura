"""Idempotently publish generated candidates to the existing review project."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zzaimy.app.db import Database
from zzaimy.dataset.authored_review import PROJECT
from zzaimy.dataset.batch_candidates import windows
from zzaimy.dataset.candidate_publish import prepare_publication
from zzaimy.dataset.ls_client import LabelStudioClient


def main():
    if Path("data/training/active-tracks.json").exists():
        raise RuntimeError("옛 CoT 게시 경로는 보관 처리되었습니다.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-conversations', type=int, default=100)
    args = parser.parse_args()
    if args.max_conversations < 1:
        parser.error('positive limit required')
    root = Path('data/training')
    root.mkdir(parents=True, exist_ok=True)
    with (root/'candidate-publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(root, args)


def run(root, args):
    db = Database(Path('data/platform/platform.db'))
    client = LabelStudioClient(db.get_setting('labelstudio_url'), db.get_setting('labelstudio_token'))
    pid = client.status(PROJECT, timeout=20).get('project_id')
    if not pid:
        raise RuntimeError('existing_review_project_required')
    existing, page = {}, 1
    while True:
        response = client._req('GET', f'/api/tasks?project={pid}&page_size=100&page={page}')
        tasks = response.get('tasks', response.get('results', [])) if isinstance(response, dict) else response
        for task in tasks:
            data = task.get('data', {})
            sid = data.get('sample_id')
            if sid:
                if sid in existing:
                    raise RuntimeError('duplicate_remote_sample_id')
                existing[sid] = data
        if len(tasks) < 100:
            break
        page += 1
    latest = {}
    counts = Counter()
    for path in sorted((root/'generated-candidates').glob('*.json')):
        try:
            value = json.loads(path.read_text())
            ctx = value['context']
            key = hashlib.sha256(json.dumps([ctx['program_id'],ctx['doc_id'],value['source_window']],
                                            sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            stamp = value.get('completed_at', 0)
            if key not in latest or stamp > latest[key][0]:
                latest[key] = (stamp, path)
        except (ValueError, KeyError):
            counts['invalid_files'] += 1
    reviews = [json.loads(p.read_text()) for p in (root/'candidate-reviews').glob('*.json')]
    for _, path in sorted(latest.values(), key=lambda pair:pair[0]):
        candidate = json.loads(path.read_text())
        if candidate['status'] != 'candidate':
            counts['held_or_error_windows'] += 1
            continue
        rows = candidate.get('rows', [])
        if rows and all(row['id'] in existing for row in rows):
            counts['already_published_conversations'] += 1
            continue
        if counts['prepared_conversations'] >= args.max_conversations:
            break
        did = candidate['context']['doc_id']
        raw = {c['id']: c['text'] for w in windows(db.list_doc_chunks(did)) for c in w}
        def resolve(doc_id, chunk_id):
            if doc_id != did or chunk_id not in raw:
                return None
            return {'text':raw[chunk_id], 'location':f"{candidate['context']['filename']} · 조각 {chunk_id}"}
        try:
            tasks = prepare_publication(candidate, resolve, reviews)
        except ValueError as exc:
            counts[str(exc)] += 1
            continue
        # Do not overwrite a task or its annotations. Changed answers need a reviewed revision.
        pending = [t for t in tasks if t['data']['sample_id'] not in existing]
        counts['prepared_conversations'] += 1
        counts['prepared_turns'] += len(pending)
        if args.apply and pending:
            client._req('POST', f'/api/projects/{pid}/import', json=pending)
            counts['published_turns'] += len(pending)
            existing.update({t['data']['sample_id']:t['data'] for t in pending})
    result = {'checked_at':time.time(), 'project_id':pid, 'applied':args.apply,
              'counts':dict(counts), 'training_approved':False}
    target = root/'candidate-publish-status.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temp.replace(target)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
