import httpx
import pytest

from zzaimy.ingest import folder_picker as fp
from tests.test_user_admin import _dev, _fake_google


def test_listing_is_folder_only_paginated_and_escapes_search(monkeypatch):
    monkeypatch.setattr(fp.gdrive, 'access_token', lambda *a: 'test-token')
    def handle(request):
        assert request.headers['authorization'] == 'Bearer test-token'
        if request.url.path.endswith('/files/root'):
            return httpx.Response(200, json={'id':'root-id', 'name':'내 드라이브', 'mimeType':fp.gdrive.FOLDER, 'capabilities':{'canAddChildren':True}})
        assert "name contains 'a\\'b'" in request.url.params['q']
        assert 'trashed = false' in request.url.params['q']
        assert request.url.params['pageToken'] == 'page2'
        return httpx.Response(200, json={'nextPageToken':'page3', 'files':[{'id':'folder1', 'name':'읽기 전용', 'capabilities':{}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        data = fp.browse('me@ync.ac.kr', q="a'b", token='page2', http=http)
    assert data['next'] == 'page3'
    assert data['items'][0]['writable'] is False


def test_invalid_or_deleted_folder_rejected(monkeypatch):
    monkeypatch.setattr(fp, '_get', lambda *a: {'trashed': True})
    with pytest.raises(ValueError):
        fp.folder('me@ync.ac.kr', 'valid')
    with pytest.raises(ValueError):
        fp._id("folder' or true")


def test_browser_uses_current_user_only_and_save_rechecks(tmp_path, monkeypatch):
    _fake_google(monkeypatch)
    c = _dev(tmp_path)
    assert c.get('/dev/google/folders?email=kim@ync.ac.kr').status_code == 404        # 쓰는 화면이 없어 10-27 에 없앴다
    db = c.app.state.db
    db.set_setting('google_account:zzdev', 'dev@ync.ac.kr')
    seen = []
    c.post("/org/create", json={"name": '행정'})
    # 부서 폴더 위치 바꾸기(부서 쪽 /team/drive/move) — 고른 폴더를 부서 계정으로 다시 보고, 쓸 수 없으면 옮기지 않는다
    from zzaimy.ingest import dept_drive
    moved = []
    monkeypatch.setattr(dept_drive, 'move', lambda db_, dept, parent, http=None: moved.append(parent) or parent)
    db.set_setting('google_root:행정', 'dept-folder')
    db.set_setting('google_root_by:행정', 'kim@ync.ac.kr')
    looked = []
    monkeypatch.setattr(fp, 'folder', lambda email, fid, *a: looked.append(email) or {'id': fid, 'name': '읽기 전용', 'writable': False})
    r = c.post('/team/drive/move', data={'dept': '행정', 'parent': 'readonly-folder'}, headers={'Accept': 'application/json'})
    assert r.status_code == 400 and moved == [] and looked == ['kim@ync.ac.kr']          # 부서 계정으로 확인
    monkeypatch.setattr(fp, 'folder', lambda *a: {'id': 'writable-folder', 'name': '행정 자료', 'writable': True})
    r = c.post('/team/drive/move', data={'dept': '행정', 'parent': 'writable-folder'}, headers={'Accept': 'application/json'})
    assert r.json()['ok'] and moved == ['writable-folder']
    monkeypatch.setattr(fp, 'browse', lambda email, *args: seen.append(email) or {'items': [{'id': 'dept-folder', 'name': 'AURA · 행정'}], 'next': '', 'current': None})
    got = c.get('/team/drive/folders', params={'dept': '행정', 'parent': 'root'}).json()
    assert seen[-1] == 'kim@ync.ac.kr' and got['items'] == []                          # 부서 폴더 자신은 목록에서 뺀다
