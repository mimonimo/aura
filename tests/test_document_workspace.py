"""문서 검토 동선: 합성 문서만 사용하며 모델·운영 서버는 호출하지 않는다."""

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app.main import create_app


@pytest.fixture
def workspace(tmp_path):
    app = create_app(
        db_path=tmp_path / 'test.db', inbox_dir=tmp_path / 'inbox',
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    client = TestClient(app)
    client.post('/projects', data={'name': '합성 프로젝트', 'sector': 'grant'})
    client.post('/upload', data={'doc_type': 'grant', 'project_id': '1'},
                files={'file': ('합성.txt', b'synthetic', 'text/plain')})
    return client, app.state.db


def test_document_keeps_project_context_and_evidence_first(workspace):
    client, _ = workspace
    page = client.get('/doc/1').text
    assert 'href="/project/1" class="btn-ghost"' in page
    assert page.index('id="documentSource"') < page.index('id="documentWork"')
    assert page.index('id="documentFeedback"') < page.index('>담당자 판정')
    assert 'id="documentWidthToggle"' in page
    assert 'aria-controls="documentSource documentWork"' in page


def test_processing_document_only_offers_original_export(workspace):
    client, db = workspace
    db.update_document(1, status='processing', ai_review=None)
    page = client.get('/doc/1').text
    assert 'data-poll="true"' in page
    assert '/doc/1/original' in page
    assert '/doc/1/export.docx' not in page
    assert '/doc/1/decision' not in page
    assert 'id="documentWidthToggle"' not in page


def test_office_preview_requires_generated_pdf(workspace, tmp_path, monkeypatch):
    from zzaimy.app import office_pdf
    client, db = workspace
    source = tmp_path / 'sheet.xlsx'
    source.write_bytes(b'synthetic')
    doc_id = db.add_document(filename='sheet.xlsx', stored_path=str(source), doc_type='grant')
    db.update_document(doc_id, status='reviewed')
    monkeypatch.setattr(office_pdf, 'soffice', lambda: '/usr/bin/soffice')
    target = office_pdf.view_path({'stored_path': str(source)})
    for content in (None, b'', b'%PDF-1.4'):
        if content is not None:
            target.write_bytes(content)
        page = client.get(f'/doc/{doc_id}').text
        assert ('id="docPdf"' in page) == bool(content)
        if not content:
            assert 'PDF 미리보기가 준비되지 않았습니다' in page
            assert f'href="/doc/{doc_id}/view"' in page


def test_empty_regulation_summary_can_be_requested(workspace):
    client, db = workspace
    doc_id = db.add_document(filename='합성기준.txt', stored_path='absent-synthetic.txt',
                             doc_type='regulation')
    db.update_document(doc_id, status='reviewed', ai_review=None, coverage=None)
    page = client.get(f'/doc/{doc_id}').text
    assert f'action="/doc/{doc_id}/analyze"' in page
    assert 'id="documentFeedback"' not in page
    assert 'id="documentWidthToggle"' not in page


def test_draft_generation_exposes_status_poll_without_inline_reload(workspace):
    client, db = workspace
    db.update_document(1, coverage='초안 작성 중입니다')
    page = client.get('/doc/1').text
    assert 'data-poll="true"' in page
    assert 'id="documentUpdate"' in page
    assert 'action="/doc/1/draft"' not in page
    assert '/static/document-workspace.js' in page
