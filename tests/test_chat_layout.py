from tests.test_dev_pages import client


def test_chat_header_has_only_document_control(client):
    response = client.get('/chat')
    assert response.status_code == 200
    header = response.text.split('<div class="chat-head-actions">', 1)[1].split('</div>', 1)[0]
    assert header.count('<button') == 1
    assert 'id="chatDocumentOpen"' in header
    assert 'href="/chat"' not in header
    assert 'id="chatHistory"' not in response.text
    tools = response.text.split('id="chatTools"', 1)[1].split('</div>', 1)[0]
    assert 'id="chatContextOpen"' in tools
    assert response.text.count('id="chatContextOpen"') == 1
    assert 'data-chat-tool="file"' in tools
