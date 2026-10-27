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


def _user_menu(page):
    return page.split('id="userPop"', 1)[1].split('</form>', 1)[0]


def test_user_menu_holds_admin_entries_and_sidebar_scrolls_once(tmp_path):
    """사용자 메뉴(10-27) — 관리자: 설정·문답 검수·테스트 보기·개발 현황·로그아웃, 검수자: 설정·문답 검수·로그아웃, 교직원: 설정·로그아웃.
    계정 관리(= 조직 관리)는 없고, 사이드바 본 메뉴·아래 묶음에도 문답 검수·테스트 보기·개발 현황이 없다. 가운데는 한 칸으로 굴러간다(안쪽 max-height 없음)."""
    app = _app(tmp_path)
    for i in range(7):
        app.state.db.create_project('grant', f'사업 {i}', owner='zzdev')
    dev = TestClient(app)
    assert _login(dev, 'zzdev', 'devpass')
    for url in ('/chat', '/dev/archive'):                       # 업무 화면과 개발 화면이 같은 사용자 메뉴
        page = dev.get(url).text
        menu = _user_menu(page)
        labels = [x.split('<span>', 1)[1].split('</span>', 1)[0] for x in menu.split('class="um-item')[1:]]
        assert labels == ['설정', '문답 검수', '테스트 보기', '개발 현황', '로그아웃'], (url, labels)
        for mark in ('menu-settings', 'menu-review', 'menu-view-as', 'menu-dev'):
            assert f'data-aura="{mark}"' in menu
        assert '계정 관리' not in page and 'href="/team?view=all"' not in page
        assert 'id="viewAsDialog"' in page                      # 메뉴의 테스트 보기가 여는 창
        rest = page.split('<aside class="sidebar"', 1)[1].split('id="userPop"', 1)[0]
        assert 'href="/review"' not in rest and '테스트 보기' not in rest
        if url == '/chat':                                      # 개발 화면은 개발 메뉴에 「개발 현황」 이 있다
            assert 'href="/dev" ' not in rest
        assert 'max-height:180px' not in page and 'max-height:140px' not in page
    page = dev.get('/chat').text
    assert 'data-side-scroll' in page and 'data-side-limit="5"' in page and 'data-side-limit="8"' in page
    assert 'data-side-more="sideProjects"' in page and '2개 더 보기' in page    # 7개 중 5개만, 나머지는 「2개 더 보기」
    assert '업무 화면으로' in dev.get('/dev/archive').text

    staff = TestClient(app)
    assert _login(staff, 'zzaimy', 'boot-pass-1')
    labels = [x.split('<span>', 1)[1].split('</span>', 1)[0] for x in _user_menu(staff.get('/chat').text).split('class="um-item')[1:]]
    assert labels == ['설정', '로그아웃']
    app.state.accounts['zzaimy']['perms'] = ['sft_review']        # 검수 권한을 받은 교직원
    labels = [x.split('<span>', 1)[1].split('</span>', 1)[0] for x in _user_menu(staff.get('/chat').text).split('class="um-item')[1:]]
    assert labels == ['설정', '문답 검수', '로그아웃']
