from tests.test_dev_pages import client


def test_shared_navigation_and_chat_scope(client):
    page = client.get('/chat').text
    assert 'href="#workspaceContent"' in page
    assert '<main id="workspaceContent" tabindex="-1">' in page
    assert 'class="chat-scope"' in page
    assert '내 범위:' in page


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
        page = client.get('/dev/train')
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
    page = client.get('/dev/quality')
    assert page.status_code == 200
    assert page.text.count('<meter ') == 1
    assert 'value="0.7"' in page.text and '70.0%' in page.text
    assert '미측정 구성' in page.text and '미측정 — 대기' in page.text


def test_quality_report_full_note_and_resolution_preserved(client):
    client.post('/upload', data={'doc_type': 'grant'}, files={'file': ('합성.pdf', b'%PDF', 'application/pdf')})
    note = '긴 품질 설명\n' * 30
    client.post('/doc/1/quality-report', data={'kind': 'table', 'note': note})
    page = client.get('/dev/quality')
    assert 'class="quality-report"' in page.text
    assert note.strip() in page.text
    assert 'class="quality-resolve"' in page.text
    rid = client.app.state.db.list_quality_reports()[0]['id']
    response = client.post(f'/dev/quality/{rid}/done', data={'fix_note': '합성 검수 완료'}, follow_redirects=False)
    assert response.status_code == 303
    assert client.app.state.db.quality_report_stats()['open'] == 0


def test_data_build_sources_have_labels_and_selection_status(client):
    page = client.get('/dev/data')
    assert page.status_code == 200
    assert 'id="datasetBuildForm"' in page.text
    assert 'id="datasetSelectionStatus" role="status"' in page.text
    assert '원천 선택"' in page.text
