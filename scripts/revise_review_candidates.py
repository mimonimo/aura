"""Apply source-checked standalone revisions; default is read-only preflight."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

REMOTE = '''
import json,sys
from pathlib import Path
from datetime import datetime
from zzaimy.app.db import Database
from zzaimy.dataset.ls_client import LabelStudioClient
from zzaimy.dataset.privacy import protect_candidate
from zzaimy.verify.numbers import verify_numbers
p=json.load(sys.stdin);ns={};exec(p['module'],ns)
db=Database(Path.cwd()/'data/platform/platform.db')
c=LabelStudioClient(db.get_setting('labelstudio_url'),db.get_setting('labelstudio_token'))
pid=c.status('ZZAIMY 근거 기반 문답 검수',timeout=20)['project_id']
if not pid:raise RuntimeError('review_project_unavailable')
tasks=[];page=1
while True:
 r=c._req('GET',f'/api/tasks?project={pid}&page_size=100&page={page}')
 rows=r.get('tasks',r.get('results',[])) if isinstance(r,dict) else r
 tasks.extend(rows)
 if len(rows)<100: break
 page+=1
by_id={t['data']['sample_id']:t for t in tasks}
def resolve(did,cid):
 ch=next((x for x in db.list_doc_chunks(did) if x['id']==cid),None)
 if not ch:return None
 return json.loads(ch['content']).get('text',ch['content']) if ch['kind']=='table' else ch['content']
revised=ns['revise_batch'](tasks,p['proposals'],resolve)
changes=[]
for sid,d in revised.items():
 t=by_id[sid]
 if all(t['data'].get(k)==d[k] for k in ('question','answer','rationale','history')) and t['data']['_record'].get('history',[])==d['_record']['history']:continue
 if protect_candidate(d)!=d:raise ValueError('privacy_hold')
 sources=list(d['_record']['source_texts']);parent=d['_record'].get('parent')
 while parent:
  previous=revised[parent];sources.extend(previous['_record']['source_texts']);parent=previous['_record'].get('parent')
 if not verify_numbers(d['answer'],sources).ok:raise ValueError('number_hold')
 changes.append((t,d))
report={'revisions':len(changes),'applied':False}
if p['apply'] and changes:
 out=Path('data/training/revision-backups')/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
 out.mkdir(parents=True,mode=0o700)
 (out/'before.json').write_text(json.dumps([t for t,d in changes],ensure_ascii=False))
 (out/'proposed.json').write_text(json.dumps([d for t,d in changes],ensure_ascii=False))
 for t,d in changes:
  current=c._req('GET',f"/api/tasks/{t['id']}")
  if current['data']!=t['data'] or current.get('annotations'):raise ValueError('concurrent_change')
 for t,d in changes:
  c._req('PATCH',f"/api/tasks/{t['id']}",json={'data':d})
  if c._req('GET',f"/api/tasks/{t['id']}")['data']!=d:raise ValueError('readback_mismatch')
 report.update(applied=True,backup=str(out))
print(json.dumps(report,ensure_ascii=False))
'''

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('proposals', type=Path)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = {'proposals': json.loads(args.proposals.read_text()), 'apply': args.apply,
               'module': (root/'src/zzaimy/dataset/revisions.py').read_text()}
    command = 'cd /home/aura/zzaimy-capstone && .venv/bin/python -c ' + shlex.quote(REMOTE)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                             'aura@192.168.16.226', command], input=json.dumps(payload), text=True)
    raise SystemExit(result.returncode)
