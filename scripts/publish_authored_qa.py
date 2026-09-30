"""직접 작성한 JSON 문답을 SSH를 통해 교내 Label Studio 누적 프로젝트에 게시.

원문은 VM에서만 읽어 Label Studio로 전달한다. 기존 태스크 삭제/변경 없음.
"""
import argparse
import json
import subprocess
from pathlib import Path
from zzaimy.dataset.authoring import validate_rows, validate_manifest


REMOTE = r'''
import json,sys
from pathlib import Path
from datetime import datetime
from zzaimy.app.db import Database
from zzaimy.dataset.ls_client import LabelStudioClient
from zzaimy.dataset.authoring import prepare_tasks
payload=json.loads(sys.stdin.read())
db=Database(Path.cwd()/'data/platform/platform.db')
def resolve(docid,cid):
  doc=db.get_document(docid)
  chunk=next((c for c in db.list_doc_chunks(docid) if c['id']==cid),None)
  if not doc or not chunk: return None
  content=chunk['content']
  if chunk['kind']=='table': content=json.loads(content).get('text',content)
  return {'text':content,'location':f"{doc['filename']} · 추출 쪽 {chunk['page_no']} · 조각 {cid}"}
tasks,report=prepare_tasks(payload['rows'],payload['manifest'],resolve,author=payload['author'])
# The complete report is returned even on a dry run; no LS writes on a hold.
if report['held'] or payload.get('dry_run'):
 print(json.dumps({'preflight':report,'published':False},ensure_ascii=False))
 sys.exit(2 if report['held'] else 0)
stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
out=Path('data/training')/('authored-qa-'+stamp);out.mkdir(mode=0o700)
(out/'tasks.json').write_text(json.dumps(tasks,ensure_ascii=False,indent=2))
client=LabelStudioClient(db.get_setting('labelstudio_url'),db.get_setting('labelstudio_token'))
pid=client.ensure_project(payload['project'],label_config=payload['config'],description='사업·문서·목차별 근거 기반 문답 누적 검수. 생성 후보이며 SFT 승인본이 아님.')
existing={}; page=1
while True:
 response=client._req('GET',f'/api/tasks?project={pid}&page_size=100&page={page}')
 current=response.get('tasks',response.get('results',[])) if isinstance(response,dict) else response
 for t in current:
  data=t.get('data',{}); existing[data.get('sample_id')]=data
 if len(current)<100: break
 page+=1
pending=[]
for task in tasks:
 data=task['data']; old=existing.get(data['sample_id'])
 if old:
  if any(old.get(k)!=data.get(k) for k in ('question','answer','evidence','path','history','kind','rationale','source','program','program_id')):
   raise ValueError('동일 sample_id의 내용이 변경되었습니다. 기존 검수본 보존을 위해 새 revision ID를 사용하세요.')
 else: pending.append(task)
if pending: client._req('POST',f'/api/projects/{pid}/import',json=pending)
progress=client.progress(pid)
print(json.dumps({'project_id':pid,'url':f'{client.base}/projects/{pid}/data','uploaded':len(pending),'skipped':len(tasks)-len(pending),'progress':progress,'saved':str(out),'preflight':report},ensure_ascii=False))
'''

CONFIG = '''<View><Header value="직접 작성 문답 — 원문 대조 검수"/>
<Text name="program" value="$program"/><Text name="path" value="$path"/><Text name="kind" value="$kind"/>
<Header value="이전 대화" size="4"/><Text name="history" value="$history"/>
<Header value="질문" size="4"/><Text name="question" value="$question"/>
<Header value="정답 후보" size="4"/><Text name="answer" value="$answer"/>
<Header value="근거 설명" size="4"/><Text name="rationale" value="$rationale"/>
<Header value="출처 위치" size="4"/><Text name="source" value="$source"/>
<Header value="원문" size="4"/><Text name="evidence" value="$evidence"/>
<Choices name="decision" toName="answer" choice="single" required="true" showInline="true"><Choice value="채택"/><Choice value="수정"/><Choice value="폐기"/></Choices>
<TextArea name="corrected" toName="answer" rows="6" editable="true" maxSubmissions="1"/>
</View>'''

from zzaimy.dataset.authored_review import upgrade_config
CONFIG = upgrade_config(CONFIG)

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path)
    p.add_argument('--project',default='ZZAIMY 근거 기반 문답 검수')
    p.add_argument('--manifest',type=Path,required=True,help='사업 ID·사업명·반입 문서 ID 목록 JSON')
    p.add_argument('--author',required=True,help='실제 생성 주체; 검수자와 별도')
    p.add_argument('--dry-run',action='store_true',help='원문 대조 검사만 수행; 게시·파일 생성 없음')
    a=p.parse_args()
    rows=json.loads(a.input.read_text())
    validate_rows(rows)
    manifest=json.loads(a.manifest.read_text())
    validate_manifest(manifest)
    import shlex
    cmd='cd /home/aura/zzaimy-capstone && .venv/bin/python -c '+shlex.quote(REMOTE)
    subprocess.run(['ssh','-o','ConnectTimeout=10','aura@192.168.16.226',cmd],
                   input=json.dumps({'rows':rows,'config':CONFIG,'project':a.project,'manifest':manifest,
                                     'author':a.author,'dry_run':a.dry_run},ensure_ascii=False),text=True,check=True)
