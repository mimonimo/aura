from fastapi.testclient import TestClient
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from tests.test_gdocs import docs_env
from zzaimy.app.main import create_app
from zzaimy.ingest import gdrive


def client_for(tmp_path, responder=None):
    return TestClient(create_app(db_path=tmp_path/'ui.db', inbox_dir=tmp_path/'inbox',
                                processor=FakeProcessor(), drafter=FakeDrafter(),
                                responder=responder or FakeResponder()))


def test_google_app_registration_lives_on_team_page(docs_env, tmp_path):
    """앱 등록은 플랫폼에 한 번 — 조직 관리(관리자) 「학교 계정 연결 현황」 에서. 옛 /dev/users 화면은 10-27 에 지웠다."""
    client = client_for(tmp_path)
    assert client.get('/dev/users', follow_redirects=False).status_code == 404
    page = client.get('/team').text
    card = page.split('id="google-links"', 1)[1].split('</div></div>', 1)[0]
    assert 'Google 앱 등록 완료' in card and 'data-modal-open="#googleAppEdit"' in card and '앱 설정 변경' in card
    modal = page.split('id="googleAppEdit"', 1)[1].split('</form>', 1)[0]
    assert 'action="/team/google/client"' in modal and 'name="client_id"' in modal and 'name="client_secret"' in modal
    assert '/dev/gdrive/callback' in modal                                   # 리디렉션 URI 를 보인다
    assert client.get('/dev/nas', follow_redirects=False).status_code == 404
    assert client.get('/dev/intake?tab=drive', follow_redirects=False).status_code == 404   # 옛 반입 화면 주소는 없앴다(10-27)
    assert client.get('/dev/google', follow_redirects=False).status_code in (404, 405)
    r = client.post('/team/google/client', data={'client_id': 'bad', 'client_secret': 'x'}, follow_redirects=False)
    assert r.headers['location'].startswith('/team?err=') and r.headers['location'].endswith('#google-links')
