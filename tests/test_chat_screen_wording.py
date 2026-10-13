"""채팅 화면 문구 — 대화 제목 정리, 조사 붙이기, 값 입력 칸 설명, 머리줄. 처음 보는 이름·값으로 확인한다."""
import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import asks, main
from zzaimy.app.chat_history import display_title


def _client(tmp_path):
    app = main.create_app(db_path=tmp_path / 'chat.db', inbox_dir=tmp_path / 'inbox',
                          processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return TestClient(app)


def test_display_title_normalizes_and_drops_project_prefix_inside_project():
    assert display_title('ＲＩＳＥ　사업  일정') == 'RISE 사업 일정'
    # 새 대화 제목의 접두어는 프로젝트 이름 앞 14자 — 그 프로젝트 안에서만 뗀다
    name = '가상 해양바이오 인재양성 선도대학 사업'
    title = f'[{name[:14]}] 예산표 정리해 줘'
    assert display_title(title, name) == '예산표 정리해 줘'
    assert display_title(title) == title                       # 프로젝트 밖(사이드바)에서는 어느 프로젝트인지 보인다
    assert display_title('[다른 사업] 질문', name) == '[다른 사업] 질문'
    assert display_title(f'[{name[:14]}]', name) == f'[{name[:14]}]'   # 접두어뿐이면 남긴다
    assert display_title('   ') == '대화'


def test_titles_on_sidebar_and_project_page(tmp_path):
    client = _client(tmp_path)
    client.post('/projects', data={'sector': 'grant', 'name': '가상 스마트팜 실증 사업'})
    client.post('/chat/send', data={'question': 'ＡＩ　교육과정 정리', 'project_id': 1})
    db = client.app.state.db
    raw = db.get_chat_session(1)['title']
    assert raw.startswith('[가상 스마트팜 실증 사업]')
    project_page = client.get('/project/1').text
    conv = project_page.split('class="project-conversations"', 1)[1].split('</div>', 1)[0]
    assert '<span>AI 교육과정 정리</span>' in conv and '[가상 스마트팜' not in conv
    # 옛 제목에 남은 전각도 화면에서는 반각으로
    db.rename_chat_session(1, '[가상 스마트팜 실증 사업] ＡＩＤ 일정')
    sidebar = client.get('/chat').text.split('id="chatSessionList"', 1)[1].split('</div>\n  </div>', 1)[0]
    assert 'AID 일정' in sidebar and 'ＡＩＤ' not in sidebar
    found = client.get('/api/chat/sessions', params={'q': ''}).json()['sessions']
    assert found[0]['title'] == '[가상 스마트팜 실증 사업] AID 일정'


def test_answers_attach_particles_and_header_is_plain(tmp_path):
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    db.add_chat(sid, 'user', '양식 깔아 줘')
    db.add_chat(sid, 'assistant', '「가상 연구소 운영계획」 을 만들고 「공통 서식」 으로 맞췄습니다.')
    page = client.get(f'/chat/{sid}').text
    assert '「가상 연구소 운영계획」을 만들고 「공통 서식」으로 맞췄습니다.' in page
    assert '최근 요청' not in page
    assert '수치와 최종 판단은 근거 문서에서 확인하세요.' in page
    # 문서 패널 여는 단추는 하나 — 열리면 숨고 닫기는 패널의 × (떠 있는 '작업 패널 열기' 단추는 없다)
    assert 'id="asideOpen"' not in page
    assert 'aria-label="문서 패널 열기" aria-expanded="false"' in page


def test_value_form_shows_hint_before_field_and_save(tmp_path):
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    db.add_chat(sid, 'user', '1절 써 줘')
    db.add_chat(sid, 'assistant', '빈 값이 있습니다.')
    long_hint = '사업단 조직도에 쓰이는 값으로, 교육과정 편성표의 학과명과 글자까지 일치해야 합니다. 약칭은 쓰지 않습니다.'
    asks.set_pending(db, sid, [{'name': '참여 학과명', 'hint': long_hint}])     # 칸은 받을 값에서 만든다
    page = client.get(f'/chat/{sid}').text
    form = page.split('chat-suggestion-form', 1)[1].split('</form>', 1)[0]
    assert f'placeholder="{long_hint}' not in form          # 설명은 칸 안 자리표시가 아니라 이름 아래 글로
    assert f'<small class="chat-form-hint"' in form and long_hint in form
    assert '>저장</button>' in form
    # 설명은 칸 이름 바로 아래(칸보다 앞), 칸은 길게 쓸 수 있는 여러 줄, 머리에 채운 수
    assert form.index('chat-form-hint') < form.index('<textarea') and '1개 중 0개 채움' in form


def test_ask_hint_is_cut_on_word_boundary():
    from zzaimy.app.korean_text import clip
    hint = '사업단 조직도에 쓰이는 값으로 교육과정 편성표의 학과명과 글자까지 일치해야 합니다'
    out = clip(hint, 40)
    assert out.endswith('…') and '합니' not in out
    assert hint.startswith(out[:-1])
    assert out[:-1].split()[-1] in hint.split()


def test_platform_documents_carry_raw_state(tmp_path):
    client = _client(tmp_path)
    client.post('/chat/send', data={'question': '첨부 확인'}, files={'attachment': ('가상 안내.pdf', b'%PDF-1.4', 'application/pdf')})
    rows = client.get('/api/chat/1/documents').json()['documents']
    assert rows and all('state' in r for r in rows)
    assert rows[0]['original_format'] == 'pdf'
