"""Plan or apply latest-only cleanup to the existing grounded review project.

Run locally with --apply to update the configured VM. Full backup precedes writes.
Project IDs, labels, answers and annotations are preserved. No project is deleted.
"""
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
payload=json.load(sys.stdin)
ns={};exec(payload['planner'],ns)
db=Database(Path.cwd()/'data/platform/platform.db')
c=LabelStudioClient(db.get_setting('labelstudio_url'),db.get_setting('labelstudio_token'))
project=c.status('ZZAIMY 근거 기반 문답 검수')
pid=project.get('project_id')
if not pid: raise RuntimeError('review_project_missing')
tasks=[];page=1
while True:
 r=c._req('GET',f'/api/tasks?project={pid}&page_size=100&page={page}')
 rows=r.get('tasks',r.get('results',[])) if isinstance(r,dict) else r
 tasks.extend(rows)
 if len(rows)<100: break
 page+=1
updates,removals=ns['plan_tasks'](tasks)
from collections import Counter
report={'project_id':pid,'before':len(tasks),'remove_ids':removals,
        'states':dict(Counter(t['data']['관리_상태'] for t in updates)),'applied':False}
if payload['apply']:
 out=Path('data/training/workspace-backups')/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
 out.mkdir(parents=True,mode=0o700)
 config=c._req('GET',f'/api/projects/{pid}')
 (out/'backup.json').write_text(json.dumps({'project':config,'tasks':tasks},ensure_ascii=False))
 # Refuse if any task changed between planning and execution.
 for old in tasks:
  now=c._req('GET',f"/api/tasks/{old['id']}")
  if now['data']!=old['data'] or now.get('annotations',[])!=old.get('annotations',[]):
   raise RuntimeError('concurrent_task_change')
 for update in updates:
  c._req('PATCH',f"/api/tasks/{update['id']}",json={'data':update['data']})
 # Backup includes originals; actual dependent tasks were checked by planner.
 for tid in removals:
  c._req('DELETE',f'/api/tasks/{tid}')
 c._req('PATCH',f'/api/projects/{pid}',json={'description':'현재 문답 검수. 관리_사업·관리_유형·관리_상태로 필터링합니다. 맥락 보강 필요 → 원문 대조 → 검수 결과 확인. SFT 승인은 별도 품질 관문을 통과해야 합니다. 구버전은 복구 백업으로 보존합니다.'})
 report.update(applied=True,backup=str(out),after=c.progress(pid))
print(json.dumps(report,ensure_ascii=False))
'''

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = 'cd /home/aura/zzaimy-capstone && .venv/bin/python -c ' + shlex.quote(REMOTE)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                             'aura@192.168.16.226', command], input=json.dumps({
        'planner': (root/'src/zzaimy/dataset/workspace.py').read_text(), 'apply': args.apply}), text=True)
    raise SystemExit(result.returncode)
