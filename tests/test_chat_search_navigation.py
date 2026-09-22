"""검색 결과는 미리보기를 거치지 않고 기존 대화 경로를 연다."""
from pathlib import Path


def test_search_results_keep_native_chat_links():
    script = (Path(__file__).parents[1] / "src/zzaimy/app/static/chat-history.js").read_text()
    assert "link.href = '/chat/' + session.id" in script
    assert "preview(session)" not in script
    assert "session-preview" not in script
    assert "link.addEventListener('click'" not in script
    assert "row.append(link, meta, actions)" in script
