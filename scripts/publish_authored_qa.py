"""직접 작성한 JSON 문답을 SSH를 통해 교내 Label Studio 누적 프로젝트에 게시.

원문은 VM에서만 읽어 Label Studio로 전달한다. 기존 태스크 삭제/변경 없음.
"""
import argparse
import json
import subprocess
from pathlib import Path


def validate_rows(rows):
    """게시 형식·대화 연결만 검사한다. 사실 정확성/개인정보 검수를 대체하지 않는다."""
    if not isinstance(rows, list) or not rows:
        raise ValueError('비어 있지 않은 문답 목록이 필요합니다.')
    by_id = {}
    questions = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('문답은 객체여야 합니다.')
        for key in ('id', 'kind', 'question', 'answer', 'rationale'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f'필수 문자열 누락: {key}')
        if row['id'] in by_id:
            raise ValueError('중복 sample_id')
        by_id[row['id']] = row
        path = row.get('path')
        if not isinstance(path, list) or not path or any(not isinstance(p, str) or not p.strip() for p in path):
            raise ValueError('목차 경로가 필요합니다.')
        refs = row.get('refs')
        if not isinstance(refs, list) or not refs:
            raise ValueError('원문 연결이 필요합니다.')
        if any(not isinstance(ref, list) or len(ref) != 2 or any(type(v) is not int or v <= 0 for v in ref) for ref in refs):
            raise ValueError('원문 연결은 양의 정수 [문서, 조각]이어야 합니다.')
        key = (row.get('parent'), ' '.join(row['question'].split()))
        if key in questions:
            raise ValueError('같은 대화 맥락의 질문 중복')
        questions.add(key)
    for row in rows:
        seen = {row['id']}
        parent = row.get('parent')
        while parent is not None:
            if not isinstance(parent, str) or parent not in by_id or parent in seen:
                raise ValueError('대화 연결 누락 또는 순환')
            seen.add(parent)
            parent = by_id[parent].get('parent')


REMOTE = r'''
import json,sys
from pathlib import Path
from datetime import datetime
from zzaimy.app.db import Database
from zzaimy.dataset.ls_client import LabelStudioClient
payload=json.loads(sys.stdin.read())
db=Database(Path.cwd()/'data/platform/platform.db')
rows=payload['rows']; by_id={r['id']:r for r in rows}; tasks=[]
for r in rows:
 evidence=[]; locations=[]
 for docid,cid in r['refs']:
  doc=db.get_document(docid)
  chunk=next((c for c in db.list_doc_chunks(docid) if c['id']==cid),None)
  if not doc or not chunk: raise ValueError('원문 연결 실패')
  content=chunk['content']
  if chunk['kind']=='table': content=json.loads(content).get('text',content)
  evidence.append(content); locations.append(f"{doc['filename']} · 추출 쪽 {chunk['page_no']} · 조각 {cid}")
 history=[]; parent=r.get('parent'); seen=set()
 while parent:
  if parent in seen or parent not in by_id: raise ValueError('대화 연결 오류')
  seen.add(parent); p=by_id[parent]; history.insert(0,{'question':p['question'],'answer':p['answer']}); parent=p.get('parent')
 tasks.append({'data':{'sample_id':r['id'],'program':'2026학년도 AID 전환 중점 전문대학 지원사업',
  'path':' → '.join(r['path']),'kind':r['kind'],'question':r['question'],'answer':r['answer'],
  'rationale':r['rationale'],'history':'\n\n'.join('질문: '+p['question']+'\n답변: '+p['answer'] for p in history) or '단독 질문',
  'source':'\n'.join(locations),'evidence':'\n\n'.join(evidence),
  '_record':dict(r,history=history,author='Codex',reviewed=False,source_texts=evidence)}})
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
  if any(old.get(k)!=data.get(k) for k in ('question','answer','evidence','path','history','kind','rationale','source','program')):
   raise ValueError('동일 sample_id의 내용이 변경되었습니다. 기존 검수본 보존을 위해 새 revision ID를 사용하세요.')
 else: pending.append(task)
if pending: client._req('POST',f'/api/projects/{pid}/import',json=pending)
progress=client.progress(pid)
print(json.dumps({'project_id':pid,'url':f'{client.base}/projects/{pid}/data','uploaded':len(pending),'skipped':len(tasks)-len(pending),'progress':progress,'saved':str(out)},ensure_ascii=False))
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
    a=p.parse_args()
    rows=json.loads(a.input.read_text())
    validate_rows(rows)
    import shlex
    cmd='cd /home/aura/zzaimy-capstone && .venv/bin/python -c '+shlex.quote(REMOTE)
    subprocess.run(['ssh','-o','ConnectTimeout=10','aura@192.168.16.226',cmd],
                   input=json.dumps({'rows':rows,'config':CONFIG,'project':a.project},ensure_ascii=False),text=True,check=True)
