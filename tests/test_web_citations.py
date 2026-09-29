from zzaimy.app.citations import linkify


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
