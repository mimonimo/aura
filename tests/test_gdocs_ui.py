from fastapi.testclient import TestClient
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from tests.test_gdocs import docs_env
from zzaimy.app.main import create_app
from zzaimy.ingest import gdrive


def client_for(tmp_path, responder=None):
    return TestClient(create_app(db_path=tmp_path/'ui.db', inbox_dir=tmp_path/'inbox',
                                processor=FakeProcessor(), drafter=FakeDrafter(),
                                responder=responder or FakeResponder()))


def test_no_account_shows_connection_instead_of_empty_form(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, 'list_accounts', lambda: [])
    page = client_for(tmp_path).get('/gdocs/work')
    assert page.status_code == 200
    assert 'Google 계정을 연결하세요' in page.text
    assert 'class="gd-open"' not in page.text
    assert '/settings#myGoogle' in page.text                  # 연결은 사용자마다 설정에서


def test_editor_actions_keep_contract_and_escape_answer(docs_env, tmp_path):
    class Responder:
        def answer(self, *args, **kwargs):
            return '<img src=x onerror=alert(1)>검토 결과'
    client = client_for(tmp_path, Responder())
    page = client.post('/gdocs/ask', data={'doc':'docA', 'account':'staff@example.ac.kr', 'question':'검토'})
    assert page.status_code == 200
    assert 'class="gd-split"' in page.text
    assert 'id="gdAnswer">&lt;img' in page.text
    assert '<img src=x' not in page.text
    assert 'data-use-answer' in page.text
    assert 'name="section"' in page.text and 'value="3"' in page.text
    assert page.text.count('data-confirm=') == 2
    assert not any(c[1].endswith(':batchUpdate') for c in docs_env[1])


def test_google_app_registration_lives_on_team_page(docs_env, tmp_path):
    """앱 등록은 플랫폼에 한 번 — 조직 관리(관리자) 「학교 계정 연결 현황」 에서. 옛 /dev/users 화면은 10-27 에 지웠다."""
    client = client_for(tmp_path)
    assert client.get('/dev/users', follow_redirects=False).status_code == 404
    page = client.get('/team').text
    card = page.split('id="google-links"', 1)[1].split('</div></div>', 1)[0]
    assert 'Google 앱 등록 완료' in card and 'data-modal-open="#googleAppEdit"' in card and '앱 설정 변경' in card
    modal = page.split('id="googleAppEdit"', 1)[1].split('</form>', 1)[0]
    assert 'action="/dev/gdrive/client"' in modal and 'name="client_id"' in modal and 'name="client_secret"' in modal
    assert '/dev/gdrive/callback' in modal                                   # 리디렉션 URI 를 보인다
    assert client.get('/dev/nas', follow_redirects=False).status_code == 404
    assert client.get('/dev/intake?tab=drive', follow_redirects=False).headers['location'] == '/dev/flow#intake'   # 옛 반입 화면은 처리 흐름의 반입 점검 칸(10-26)
    assert client.get('/dev/google', follow_redirects=False).status_code in (404, 405)
    r = client.post('/dev/gdrive/client', data={'client_id': 'bad', 'client_secret': 'x'}, follow_redirects=False)
    assert r.headers['location'].startswith('/team?err=') and r.headers['location'].endswith('#google-links')
