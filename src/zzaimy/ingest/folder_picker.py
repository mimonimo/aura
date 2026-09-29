"""Read-only folder navigation using the signed-in user's Google authorization."""
import re

from zzaimy.ingest import gdrive


def _id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise ValueError('폴더 정보가 올바르지 않습니다.')
    return value


def _get(email, path, params, http=None):
    client = http or gdrive._http()
    response = client.get(gdrive.API + path, params=params,
                          headers={'Authorization': 'Bearer ' + gdrive.access_token(email, client)})
    if response.status_code != 200:
        raise ValueError('폴더를 불러오지 못했습니다. 학교 계정 연결과 접근 권한을 확인해 주세요.')
    return response.json()


def folder(email, fid, http=None):
    data = _get(email, '/files/' + _id(fid), {
        'fields': 'id,name,mimeType,trashed,capabilities(canAddChildren)',
        'supportsAllDrives': 'true'}, http)
    if data.get('trashed') or data.get('mimeType') != gdrive.FOLDER:
        raise ValueError('사용 가능한 폴더가 아닙니다.')
    return {'id': data['id'], 'name': data['name'],
            'writable': data.get('capabilities', {}).get('canAddChildren') is True}


def browse(email, parent='root', q='', token='', http=None):
    if parent == 'drives':
        data = _get(email, '/drives', {'pageSize': 50, 'pageToken': token,
                    'fields': 'nextPageToken,drives(id,name)'}, http)
        return {'items': data.get('drives', []), 'next': data.get('nextPageToken', ''), 'current': None}
    current = folder(email, parent, http)
    query = f"'{_id(parent)}' in parents and mimeType = '{gdrive.FOLDER}' and trashed = false"
    if q.strip():
        safe = q.strip()[:100].replace('\\', '\\\\').replace("'", "\\'")
        query += f" and name contains '{safe}'"
    data = _get(email, '/files', {'q': query, 'pageSize': 50, 'pageToken': token,
        'fields': 'nextPageToken,files(id,name,capabilities(canAddChildren))', 'orderBy': 'name',
        'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}, http)
    return {'items': [{'id': f['id'], 'name': f['name'],
                       'writable': f.get('capabilities', {}).get('canAddChildren') is True}
                      for f in data.get('files', [])],
            'next': data.get('nextPageToken', ''), 'current': current}
