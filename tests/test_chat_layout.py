from tests.test_dev_pages import client


def test_chat_header_has_only_document_control(client):
    response = client.get('/chat')
    assert response.status_code == 200
    header = response.text.split('<div class="chat-head-actions">', 1)[1].split('</div>', 1)[0]
    assert header.count('<button') == 1
    assert 'id="chatDocumentOpen"' in header
    assert 'href="/chat"' not in header
    assert 'id="chatHistory"' not in response.text
    assert 'id="chatContextOpen"' not in response.text
    # 답변 방식은 고르기 하나(문서 작업·기본 대화·웹 검색), 클립 단추는 파일 첨부만
    mode = response.text.split('id="answerMode"', 1)[1].split('</select>', 1)[0]
    assert [o.split('>', 1)[1].split('<', 1)[0] for o in mode.split('<option')[1:]] == ['문서 작업', '기본 대화', '웹 검색']
    assert 'value="1">웹 검색' in mode
    assert 'id="chatTools"' not in response.text and 'id="webSearchToggle"' not in response.text
    assert 'id="modeBadge"' not in response.text
    assert 'aria-label="파일 또는 사진 첨부"' in response.text
    assert 'chatKeyboardHint' not in response.text
    assert 'Enter 전송 · Shift+Enter 줄바꿈' not in response.text
