import pytest
from fastapi.testclient import TestClient
from tests.test_accounts import _app, _login


@pytest.mark.parametrize('url,active', [('/dev/archive','원본 관리'),('/dev/archive/doc/1','원본 관리'),('/dev/docs','논문·설계 문서')])
def test_developer_sidebar_replaces_workspace_menu(tmp_path, url, active):
    app = _app(tmp_path)
    app.state.db.add_document('시험.pdf', '/x/a.pdf', doc_type='recruit')
    c = TestClient(app)
    assert _login(c, 'zzdev', 'devpass')
    response = c.get(url)
    assert response.status_code == 200
    sidebar = response.text.split('<aside class="sidebar"', 1)[1].split('</aside>', 1)[0]
    assert 'data-developer-navigation' in sidebar
    assert f'aria-current="page">{active}</a>' in sidebar
    assert 'id="chatSessionList"' not in sidebar
    assert '업무 화면으로' in sidebar
    assert '데이터·학습' in sidebar
    assert '문답 데이터·검수' not in sidebar
    assert 'href="/dev/train?tab=models"' not in sidebar
    assert 'href="/dev/train?tab=exports"' not in sidebar
    # 세 묶음(개발·관리·지표)만, 업무 화면에 있는 것(그래프 탐색·계정 연결)은 두지 않는다(10-27)
    assert all(f'>{g}<' in sidebar for g in ('개발', '관리', '지표'))
    for gone in ('href="/graph/explore"', 'href="/dev/users"', 'href="/dev/db"', '문서 반입·읽기', '검색·지식', '보안·관리'):
        assert gone not in sidebar, gone


def test_staff_workspace_keeps_normal_navigation(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, 'zzaimy', 'boot-pass-1')
    page = c.get('/settings').text
    assert 'data-developer-navigation' not in page
    assert 'id="chatSessionList"' in page
