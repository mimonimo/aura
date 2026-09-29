import pytest
from fastapi.testclient import TestClient
from tests.test_accounts import _app, _login


@pytest.mark.parametrize('url,active', [('/dev/users','사용자·계정'),('/dev/db','원천 데이터'),('/dev/docs','논문·설계 문서')])
def test_developer_sidebar_replaces_workspace_menu(tmp_path, url, active):
    c = TestClient(_app(tmp_path))
    assert _login(c, 'zzdev', 'devpass')
    response = c.get(url)
    assert response.status_code == 200
    sidebar = response.text.split('<aside class="sidebar"', 1)[1].split('</aside>', 1)[0]
    assert 'data-developer-navigation' in sidebar
    assert f'aria-current="page">{active}</a>' in sidebar
    assert 'id="chatSessionList"' not in sidebar
    assert '업무 화면으로' in sidebar
    assert '문답 데이터·검수' in sidebar


def test_staff_workspace_keeps_normal_navigation(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, 'zzaimy', 'boot-pass-1')
    page = c.get('/settings').text
    assert 'data-developer-navigation' not in page
    assert 'id="chatSessionList"' in page
