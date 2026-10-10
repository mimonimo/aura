from zzaimy.app.citations import linkify


def test_google_output_links_are_compact_and_numbered():
    result = linkify('<p>작업본 https://docs.google.com/document/d/test_doc/edit</p>'
                     '<p>완성본 https://drive.google.com/file/d/test_file/view.</p>'
                     '<p>https://docs.google.com/document/d/test_doc/edit</p>', [])
    assert result.count('>[문서 1]</a>') == 2
    assert '>[파일 2]</a>.' in result
    assert 'data-document-link="true"' in result
    assert 'href="https://docs.google.com/document/d/test_doc/edit"' in result


def test_google_links_leave_code_and_existing_links_unchanged():
    url = 'https://docs.google.com/document/d/test_doc/edit'
    for html in [f'<pre>{url}</pre>', f'<code>{url}</code>',
                 f'<a href="{url}">작업본</a>',
                 '<p>https://docs.google.com.evil.test/document/d/test_doc/edit</p>']:
        assert linkify(html, []) == html


def test_legacy_web_source_title_link():
    html = '<p style="margin:4px">[1] GPU 비교 &amp; 도입 — https://example.com/%EA%B0%80?q=1&amp;x=2</p>'
    result = linkify(html, [])
    assert 'target="_blank"' in result
    assert 'rel="noopener noreferrer"' in result
    assert '>GPU 비교 &amp; 도입</a>' in result
    assert 'href="https://example.com/%EA%B0%80?q=1&amp;x=2"' in result
    assert ' — https' not in result


def test_source_escaping_and_no_nested_links():
    result = linkify('<p>[2] &lt;img src=x onerror=alert(1)&gt; — https://example.com/</p>', [])
    assert '<img' not in result
    result = linkify('<p>[1] 테스트 기준 문서 — https://example.com/</p>',
                     [{'title': '테스트 기준 문서', 'doc_id': 7}])
    assert result.count('<a ') == 1
    unsafe = '<p>[1] 나쁜 출처 — javascript:alert(1)</p>'
    assert linkify(unsafe, []) == unsafe


def _web_answer(body: str) -> str:
    return (f'<p>{body}</p><p>출처(외부 검색 · duckduckgo):</p>'
            '<p>[1] 첫 출처 — https://example.com/a?x=1&amp;y=2</p>'
            '<p>[2] 둘째 &lt;b&gt; 출처 — https://example.org/b</p>')


def test_inline_markers_link_to_matching_web_source():
    result = linkify(_web_answer('빠르다[1][2]. 또 [9] 는 없다.'), [])
    body = result.split('출처(외부 검색')[0]
    assert body.count('class="cite-ref"') == 2
    assert ('<a class="cite-ref" href="https://example.com/a?x=1&amp;y=2" target="_blank" '
            'rel="noopener noreferrer"') in body
    assert 'href="https://example.org/b"' in body
    assert '[9]' in body and 'cite-ref" href="#' not in body   # 출처 없는 번호는 글자 그대로
    assert '&lt;b&gt;' in body and '<b>' not in body          # 제목은 이스케이프된 채 title 에만
    # 출처 목록 줄은 그대로 한 번만 링크
    assert result.count('class="web-citation"') == 2
    assert result.split('출처(외부 검색')[1].count('cite-ref') == 0


def test_inline_markers_skip_links_code_and_answers_without_sources():
    html = '<p>근거[1]</p><pre>[1]</pre>'
    assert linkify(html, []) == html                          # 출처 목록이 없으면 그대로
    result = linkify(_web_answer('<code>[1]</code> <a href="/x">[2]</a> 본문[2]'), [])
    assert '<code>[1]</code>' in result
    assert '<a href="/x">[2]</a>' in result
    assert result.split('출처(외부 검색')[0].count('cite-ref') == 1
