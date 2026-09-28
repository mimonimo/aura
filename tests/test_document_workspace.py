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
    assert 'id="documentFeedback"' not in page
    assert '>담당자 판정' not in page
    assert '/doc/1/decision' not in page
    assert 'id="reviewMemo"' not in page
    assert 'id="documentWidthToggle"' in page
    assert 'aria-controls="documentSource documentWork"' in page
    assert 'class="document-columns source-expanded"' in page
    assert 'hidden>나란히 보기</button>' in page


def test_memo_preserves_saved_reviews_and_admission_decisions(workspace):
    client, db = workspace
    db.add_review(1, '기존 근거 메모 보존')
    page = client.get('/doc/1').text
    assert db.get_reviews(1)[0]['opinion'] == '기존 근거 메모 보존'
    assert 'action="/doc/1/review"' not in page
    assert 'id="reviewOpinion"' not in page
    for kind in ('recruit', 'admission'):
        did = db.add_document(filename='합성.txt', stored_path='absent', doc_type=kind)
        db.update_document(did, status='reviewed')
        assert f'action="/doc/{did}/decision"' in client.get(f'/doc/{did}').text


def test_processing_document_only_offers_original_export(workspace):
    client, db = workspace
    db.update_document(1, status='processing', ai_review=None)
    page = client.get('/doc/1').text
    assert 'data-poll="true"' in page
    assert '/doc/1/original' in page
    assert '/doc/1/export.docx' not in page
    assert '/doc/1/decision' not in page
    assert 'id="documentWidthToggle"' not in page


def test_library_lists_all_registered_types_without_search(workspace):
    client, db = workspace
    criteria = db.add_document(filename='library-criteria.txt', stored_path='absent', doc_type='regulation')
    extract = db.add_document(filename='library-extract.txt', stored_path='absent', doc_type='ocr')
    page = client.get('/inbox?type=all').text
    for doc_id in (1, criteria, extract):
        assert f'href="/doc/{doc_id}"' in page
    filtered = client.get('/inbox?type=all&group=criteria').text
    assert f'href="/doc/{criteria}"' in filtered
    assert f'href="/doc/{extract}"' not in filtered
    assert 'name="group"' in filtered


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
