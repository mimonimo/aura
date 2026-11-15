"""사이드바 목록 이름 가운데 줄임·공유한 사람 줄·가운데 칸 경계(11-15)."""
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import project_access as pa
from zzaimy.app.side_labels import split_names


def test_split_keeps_the_part_that_differs():
    names = ['2026 전문대학 특성화(4번 재현 8차)', '2026 전문대학 특성화(4번 재현 9차)',
             '2026 전문대학 특성화(4번 재현 10차)', '출장 결과 보고서']
    parts = split_names(names)
    assert [h + t for h, t in parts] == names                     # 나눠도 글자는 그대로
    assert [t for _, t in parts[:3]] == ['재현 8차)', '재현 9차)', '재현 10차)']   # 짧은 차이는 앞 낱말 하나를 더
    assert parts[3] == ('출장 결과 보고서', '')                    # 겹치지 않는 이름은 나누지 않는다


def test_split_uses_the_earliest_difference_among_neighbours():
    # 「8차 1단계」 는 「8차 2단계」 와 늦게, 「9차 1단계」 와 일찍 갈린다 — 일찍 갈리는 자리부터 남겨야 셋이 다 구별된다
    parts = split_names(['특성화 4번 재현 8차 1단계 표 채우기', '특성화 4번 재현 8차 2단계 표 채우기', '특성화 4번 재현 9차 1단계 표 채우기'])
    assert [t for _, t in parts] == ['8차 1단계 표 채우기', '8차 2단계 표 채우기', '9차 1단계 표 채우기']


def test_split_edge_cases():
    assert split_names([]) == []
    assert split_names(['같은 이름 그대로입니다', '같은 이름 그대로입니다']) == [('같은 이름 그대로입니다', '')] * 2   # 다른 부분이 없다
    assert split_names(['짧은 이름', '짧은 이름2']) == [('짧은 이름', ''), ('짧은 이름2', '')]                       # 겹침이 짧다
    head, tail = split_names(['사업계획서초안작성A', '사업계획서초안작성B'])[0]               # 띄어 쓰지 않은 이름
    assert head + tail == '사업계획서초안작성A' and tail.endswith('A') and len(head) >= 4
    assert split_names(['가나다라마바사아 하나', None])[1] == ('', '')


def _shared_app(tmp_path):
    app = _app(tmp_path)
    acc = app.state.accounts
    acc['tester1'] = dict(acc['zzaimy'], name='시험 담당자1')
    acc['tester2'] = dict(acc['zzaimy'], name='박지원')
    db = app.state.db
    for n, o in [('2026 전문대학 특성화(4번 재현 8차)', 'tester1'), ('2026 전문대학 특성화(4번 재현 9차)', 'tester1'),
                 ('출장 결과 보고서', 'tester2')]:
        pa.set_shares(db, db.create_project('grant', n, owner=o), 'shared', [{'to': 'user:zzaimy', 'level': 'view'}])
    for t in ('특성화 4번 재현 8차 1단계 표 채우기', '특성화 4번 재현 9차 1단계 표 채우기', '규정 질문'):
        db.create_chat_session(t, owner='zzaimy')
    return app


def test_sidebar_shared_projects_split_names_and_owner_line(tmp_path):
    c = TestClient(_shared_app(tmp_path))
    assert _login(c, 'zzaimy', 'boot-pass-1')
    page = c.get('/chat').text
    shared = page.split('id="sideShared"', 1)[1].split('</nav>', 1)[0]
    rows = shared.split('<a ')[1:]
    assert len(rows) == 3                                           # 최근 것부터: 출장, 9차, 8차
    assert 'title="출장 결과 보고서 (박지원 님이 공유)"' in rows[0] and 'side-split' not in rows[0]
    assert '<span class="side-tail"><span class="side-sp"> </span>재현 9차)</span>' in rows[1] and '<span class="side-head">2026 전문대학 특성화(4번</span>' in rows[1]
    assert 'title="2026 전문대학 특성화(4번 재현 9차) (시험 담당자1 님이 공유)"' in rows[1]
    # 공유한 사람은 이름 아래 작은 줄 — 바로 위 줄과 같은 사람이면 더 옅게
    assert 'class="side-owner"' in rows[0] and 'class="side-owner"' in rows[1] and 'class="side-owner is-repeat"' in rows[2]
    assert 'side-meta' not in shared                                # 오른쪽 이름 칸은 없앴다
    chats = page.split('id="chatSessionList"', 1)[1].split('side-more-row', 1)[0]
    assert '<span class="side-session-title side-split">' in chats and '<span class="side-sp"> </span>9차 1단계 표 채우기</span>' in chats
    assert '<span class="side-session-title">규정 질문</span>' in chats
    # 가운데 칸을 굴리면 위 메뉴와 경계·잘린 줄 흐림(조직 관리 밑에 잘린 글자가 겹쳐 보이던 것)
    assert '.sidebar:has(.side-scroll.is-scrolled) .side-top' in page and "classList.toggle('is-scrolled'" in page
    assert "classList.toggle('has-more-below'" in page
    # 좁은 칸: 앞부분이 네 글자 폭도 안 남으면 감추고 「…뒷부분」, 잘린 앞부분은 말줄임표까지만 폭(빈틈 없음)
    assert '.side-split.head-hidden .side-tail::before { content: "…"; }' in page and "sp.classList.add('head-hidden')" in page


def test_chat_sessions_api_carries_split_for_sidebar_refresh(tmp_path):
    c = TestClient(_shared_app(tmp_path))
    assert _login(c, 'zzaimy', 'boot-pass-1')
    rows = {r['title']: r for r in c.get('/api/chat/sessions').json()['sessions']}
    assert rows['특성화 4번 재현 9차 1단계 표 채우기']['side_tail'] == '9차 1단계 표 채우기'
    assert rows['특성화 4번 재현 9차 1단계 표 채우기']['side_head'] == '특성화 4번 재현 '
    assert rows['규정 질문']['side_tail'] == ''
