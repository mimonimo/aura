from tests.test_dev_pages import client


def test_project_context_and_unified_intake(client):
    client.post('/projects', data={'sector': 'grant', 'name': '화면 검수'})
    client.post('/project/1/notes', data={'content': '첫 줄\n두 번째 줄'})
    page = client.get('/project/1?bundle=2+3').text
    assert 'class="project-note-compose"' in page
    assert 'aria-describedby="noteHelp"' in page
    assert '첫 줄\n두 번째 줄' in page
    assert page.index('id="noteInput"') < page.index('id="noteList"')
    assert 'id="intakeFile" multiple' in page
    assert 'id="bundleForm"' not in page
    assert '묶음 접수' not in page
    assert '그래프에서 보기' not in page
    assert '기준 문서 2건, 접수 문서 3건' in page
    assert '연결된 기준 문서' in page
    assert 'class="project-overview-grid"' in page
    assert 'id="projectTabDocs"' not in page
    assert page.count('name="question"') >= 1


def test_linked_criteria_has_readable_title_and_separate_actions(client):
    client.post('/projects', data={'sector': 'grant', 'name': '기준 검수'})
    client.post('/criteria/upload', data={'sector': 'grant'},
                files={'file': ('긴 사업 공고문.pdf', b'%PDF', 'application/pdf')})
    client.post('/project/1/criteria', data={'criteria': ['1']})
    page = client.get('/project/1').text
    assert 'class="project-criterion-title"' in page
    assert 'class="project-criterion-open project-action-link"' in page
    assert '원본 문서는 유지됩니다.' in page
