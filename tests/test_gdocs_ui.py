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
    assert '/dev/nas#googleConnection' in page.text


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


def test_connection_panel_and_disconnect_confirmation(docs_env, tmp_path):
    page = client_for(tmp_path).get('/dev/nas')
    assert page.status_code == 200
    assert 'id="googleConnection"' in page.text
    assert 'class="gd-connect-grid"' in page.text
    assert '연결 해제' in page.text and 'data-confirm=' in page.text
    assert '편집 권한 확인 필요' in page.text
