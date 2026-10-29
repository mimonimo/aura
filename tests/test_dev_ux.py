from tests.test_dev_pages import client


def test_shared_navigation_and_chat_scope(client):
    page = client.get('/chat').text
    assert 'href="#workspaceContent"' in page
    assert '<main id="workspaceContent" tabindex="-1">' in page
    assert 'class="chat-scope"' in page
    assert '검색 범위:' in page


def test_stage_model_rows_do_not_nest_and_help_copy_is_removed(client, monkeypatch, tmp_path):
    from html.parser import HTMLParser
    from zzaimy.app import main
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    class Rows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.rows = []
            self.save_parents = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'div':
                classes = attrs.get('class', '').split()
                if 'use-row' in classes:
                    self.rows.append(list(self.stack))
                if attrs.get('id') == 'useBar':
                    self.save_parents = list(self.stack)
                self.stack.append(classes)

        def handle_endtag(self, tag):
            if tag == 'div' and self.stack:
                self.stack.pop()

    lc.configure(tmp_path / 'layout-connections.json')
    model_config.set_override('', '')
    try:
        conn = lc.add('검수 서버', 'vllm', 'http://test.invalid/v1', 'long-model-name', '')
        lc.activate(conn['id'])
        monkeypatch.setattr(main, '_live_models_cached', lambda *a, **kw: {
            'ok': True, 'models': [{'id': 'long-model-name'}], 'error': ''})
        page = client.get('/dev/tools')
        assert page.status_code == 200
        parsed = Rows()
        parsed.feed(page.text)
        assert len(parsed.rows) == len(lc.ROLES)
        assert all(not any('use-row' in p for p in parents) for parents in parsed.rows)
        assert parsed.save_parents is not None
        assert not any('use-row' in p or 'use-list' in p for p in parsed.save_parents)
        assert '모델 목록·API 키·삭제는 서버별 설정' not in page.text
    finally:
        lc.configure(tmp_path / 'empty-connections.json')
        model_config.set_override('', '')
        model_config.reset_status_cache()


def test_quality_chart_uses_measured_values_and_skips_unmeasured(client, monkeypatch):
    from zzaimy.eval import retrieval_eval
    monkeypatch.setattr(retrieval_eval, 'dashboard_state', lambda _: {
        'running': None, 'query_set_missing': True, 'query_set_message': '합성 검수',
        'last_error': None, 'measured_at_display': '검수 시각',
        'result': {'n_queries': 10, 'n_chunks': 100, 'embedding_model': 'test', 'notes': [], 'rows': [
            {'method': '측정 구성', 'production': True, 'recall_at_1': .4, 'recall_at_5': .7, 'recall_at_10': .8, 'mrr_at_10': .5, 'n': 10},
            {'method': '미측정 구성', 'production': False, 'recall_at_1': None, 'recall_at_5': None, 'note': '대기', 'n': 0},
        ]},
    })
    page = client.get('/dev/quality/regulation')                         # 방식별 전체 표는 자세히 화면(10-27)
    assert page.status_code == 200
    # R@5 막대는 표와 같은 수치를 두 번 보여 줘 뺐다(10-27) — 표 한 곳에 잰 값만
    assert '<meter ' not in page.text
    assert '0.700' in page.text
    assert '미측정 구성' in page.text and '미측정 (대기)' in page.text


def test_quality_report_full_note_and_resolution_preserved(client):
    client.post('/upload', data={'doc_type': 'grant'}, files={'file': ('합성.pdf', b'%PDF', 'application/pdf')})
    note = '긴 품질 설명\n' * 30
    client.app.state.db.add_quality_report(1, 'table', note, reporter='kim')
    page = client.get('/dev/quality')
    assert 'class="quality-report"' in page.text
    assert note.strip() in page.text
    assert 'class="quality-resolve"' in page.text
    rid = client.app.state.db.list_quality_reports()[0]['id']
    response = client.post(f'/dev/quality/{rid}/done', data={'fix_note': '합성 검수 완료'}, follow_redirects=False)
    assert response.status_code == 303
    assert client.app.state.db.quality_report_stats()['open'] == 0


def test_data_build_sources_are_gone_with_legacy_view(client):
    """「이전 기록·이력」(기록 기반 후보 생성 보조 경로 포함)은 10-25 에 지웠다 — 옛 view=legacy 주소도 기본 화면 그대로."""
    for url in ('/dev/train', '/dev/train?view=legacy'):
        page = client.get(url)
        assert page.status_code == 200
        assert 'id="datasetBuildForm"' not in page.text and 'id="datasetSelectionStatus"' not in page.text
        assert '이전 기록·이력' not in page.text
