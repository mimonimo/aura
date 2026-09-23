"""대화별 Google Docs 연결. 본문은 요청마다 읽고 연결 식별자만 보관한다."""
import json
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from zzaimy.ingest import gdocs, gdrive
from zzaimy.app import access_guard

router = APIRouter()


def owned(db, sid, owner):
    session = db.get_chat_session(sid)
    if not owner or not session or session.get('owner') != owner:
        raise HTTPException(404, '대화를 찾을 수 없습니다.')


def binding(db, sid, owner):
    owned(db, sid, owner)
    return json.loads(db.get_setting(f'chat_google_doc:{sid}', '{}') or '{}')


def material(db, sid, owner):
    link = binding(db, sid, owner)
    if not link:
        return ''
    info = gdocs.get(link['account'], link['doc'])
    text = info['text']
    suffix = '\n[문서가 길어 앞부분 24,000자만 제공됨]' if len(text) > 24000 else ''
    return f"[연결된 Google Docs: {info['title']}]\n{text[:24000]}{suffix}"


def identity(request):
    owner = getattr(request.state, 'user', None)
    if not owner:
        raise HTTPException(401, '로그인이 필요합니다.')
    return request.app.state.db, owner


def read_document(account, doc):
    try:
        return gdocs.get(account, doc)
    except Exception as exc:
        raise HTTPException(400, '문서를 읽지 못했습니다. 계정 권한과 문서 주소를 확인하세요.') from exc


@router.get('/api/chat-documents/accounts')
def accounts(request: Request):
    identity(request)
    return {'accounts': [{'email': a['email'], 'docs_ok': gdocs.has_docs_scope(a['email'])}
                         for a in gdrive.list_accounts()]}


@router.post('/api/chat-documents/connect')
def connect(request: Request, doc: str = Form(...), account: str = Form(...),
            session_id: int | None = Form(None), project_id: int | None = Form(None)):
    db, owner = identity(request)
    if session_id:
        owned(db, session_id, owner)
    if project_id:
        project = db.get_project(project_id)
        if not project or project.get('owner') != owner:
            raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
    if account not in {a['email'] for a in gdrive.list_accounts()}:
        raise HTTPException(400, '연결된 계정을 선택하세요.')
    info = read_document(account, doc)
    did = gdocs.doc_id(doc)
    if not session_id:
        session_id = db.create_chat_session(info['title'], project_id=project_id, owner=owner)
    db.set_setting(f'chat_google_doc:{session_id}', json.dumps({'doc':did, 'account':account}))
    return {'session_id': session_id}


@router.get('/api/chat-documents/{sid}')
def current(request: Request, sid: int):
    db, owner = identity(request)
    link = binding(db, sid, owner)
    if not link:
        return {'connected':False}
    info = read_document(link['account'], link['doc'])
    return {'connected':True, **link, 'title':info['title'], 'embed_url':gdocs.embed_url(link['doc']),
            'sections':[{'index':s['index'],'heading':s['heading']} for s in info['sections']]}


@router.delete('/api/chat-documents/{sid}')
def disconnect(request: Request, sid: int):
    db, owner = identity(request)
    owned(db, sid, owner)
    db.set_setting(f'chat_google_doc:{sid}', '{}')
    return {'ok':True}


@router.post('/api/chat-documents/{sid}/insert')
def insert(request: Request, sid: int, section: int = Form(...), text: str = Form(...),
           confirmed: bool = Form(False)):
    db, owner = identity(request)
    link = binding(db, sid, owner)
    if not link or not confirmed:
        raise HTTPException(400, '문서 연결과 삽입 확인이 필요합니다.')
    if not text.strip() or len(text) > 50000:
        raise HTTPException(400, '삽입할 내용은 1~50,000자로 입력하세요.')
    try:
        return gdocs.insert_into_section(link['account'], link['doc'], section, text,
                user=owner, data_dir=Path(db.path).parent, scrub=access_guard.scrub)
    except Exception as exc:
        raise HTTPException(400, '삽입하지 못했습니다. 문서 상태를 확인한 후 다시 시도하세요.') from exc
