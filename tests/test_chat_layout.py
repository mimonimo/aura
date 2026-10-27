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
    # 답변 방식은 토글 하나(일반·RAG), 클립 단추는 파일 첨부만. 관리자 검색 실험 고르기는 입력줄에 없다
    mode = response.text.split('class="chat-mode-seg"', 1)[1].split('</div>', 1)[0]
    assert [b.split('>', 1)[1].split('<', 1)[0] for b in mode.split('<button')[1:]] == ['일반', 'RAG']
    assert 'name="retrieval"' not in response.text and 'id="retrievalMode"' not in response.text
    assert 'id="chatTools"' not in response.text and 'id="webSearchToggle"' not in response.text
    assert 'id="modeBadge"' not in response.text
    assert 'aria-label="파일 또는 사진 첨부"' in response.text
    assert 'chatKeyboardHint' not in response.text
    assert 'Enter 전송 · Shift+Enter 줄바꿈' not in response.text
