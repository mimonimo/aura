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
    assert c.get('/dev/google/folders?email=kim@ync.ac.kr').status_code == 409
    db = c.app.state.db
    db.set_setting('google_account:zzdev', 'dev@ync.ac.kr')
    seen = []
    monkeypatch.setattr(fp, 'browse', lambda email, *args: seen.append(email) or {'items':[], 'next':'', 'current':None})
    assert c.get('/dev/google/folders?email=kim@ync.ac.kr').status_code == 200
    assert seen == ['dev@ync.ac.kr']
    db.set_setting('google_root:행정', 'old-folder')
    monkeypatch.setattr(fp, 'folder', lambda *a: {'id':'readonly-folder', 'name':'읽기 전용', 'writable':False})
    r = c.post('/dev/google/dept', data={'dept':'행정','root':'readonly-folder'}, follow_redirects=False)
    assert 'err=' in r.headers['location']
    assert db.get_setting('google_root:행정') == 'old-folder'
    monkeypatch.setattr(fp, 'folder', lambda *a: {'id':'writable-folder', 'name':'행정 자료', 'writable':True})
    r = c.post('/dev/google/dept', data={'dept':'행정','root':'writable-folder'}, follow_redirects=False)
    assert 'ok=' in r.headers['location']
    assert db.get_setting('google_root:행정') == 'writable-folder'
