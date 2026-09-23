from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from tests.test_dev_pages import client


def test_storage_has_one_entry_and_docs_editor_stays_separate(client):
    result = client.get('/connections')
    assert result.status_code == 200
    assert result.text.count('href="/dev/nas"') == 1
    assert '<h2>문서 가져오기</h2>' in result.text
    assert 'NAS·공유 폴더·Google Drive 연결' in result.text
    assert 'href="/gdocs/work"' in result.text


def test_non_admin_storage_entry_has_no_admin_link():
    root = Path(__file__).parents[1] / 'src/zzaimy/app/templates'
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '{% block content %}{% endblock %}'}),
        FileSystemLoader(root),
    ]), autoescape=True)
    page = env.get_template('connections.html').render(request=SimpleNamespace(state=SimpleNamespace(role='staff')))
    assert 'href="/dev/nas"' not in page
    assert page.count('관리자 설정 필요') == 1
    assert 'href="/gdocs/work"' in page
