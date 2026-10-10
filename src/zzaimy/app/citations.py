"""답변 안의 문서 이름을 그 문서로 가는 링크로 바꾼다.

왜: 에이전트가 "「영남이공대학교 산학협력단 운영 규정」 제4조에 따르면…" 이라고 답해도
담당자는 그 문서를 다시 찾아 들어가야 했다. 답변이 대는 근거는 눌러서 바로 갈 수 있어야 한다.

문서 이름은 근거 목록(sources)에 있는 것만 쓴다 — 모델이 지어낸 이름은 링크가 되지 않는다.
링크는 문서 화면의 문서 안 검색으로 이어져(#q=) 그 대목이 바로 보인다.
"""
from __future__ import annotations

import re
from html import escape, unescape
from urllib.parse import quote, urlsplit

_TAG = re.compile(r"(<[^>]+>)")


def document_links(html: str) -> str:
    """Compact output links without touching existing anchors or code."""
    pattern = re.compile(r'https://(?:docs\.google\.com/document/d/|drive\.google\.com/file/d/)[A-Za-z0-9_-]+[^\s<>"\]\)]*')
    numbers: dict[str, int] = {}

    def replace(match):
        raw = match[0]
        url = unescape(raw.rstrip('.,;。'))
        parsed = urlsplit(url)
        kind = '문서' if parsed.hostname == 'docs.google.com' else '파일'
        key = parsed.hostname + parsed.path.split('/d/')[1].split('/')[0]
        number = numbers.setdefault(key, len(numbers) + 1)
        return (f'<a class="chat-output-link" data-document-link="true" '
                f'href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
                f'title="{kind} {number} 미리보기">[{kind} {number}]</a>'
                + raw[len(raw.rstrip('.,;。')):])

    parts = _TAG.split(html)
    blocked = []
    for i, part in enumerate(parts):
        if part.startswith('<'):
            tag = re.match(r'<(/?)(a|pre|code|script|style)\b', part, re.I)
            if tag:
                if tag[1]:
                    if blocked and blocked[-1] == tag[2].lower():
                        blocked.pop()
                else:
                    blocked.append(tag[2].lower())
        elif not blocked:
            parts[i] = pattern.sub(replace, part)
    return ''.join(parts)


def web_links(html: str) -> str:
    """저장된 웹 출처 줄도 제목 링크로 표시한다. 원본 URL은 데이터에 보존."""
    def replace(match):
        number, title, raw_url = match.groups()
        url = unescape(raw_url).strip()
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
                return match.group(0)
        except ValueError:
            return match.group(0)
        title = unescape(re.sub(r'<[^>]+>', '', title)).strip()
        return (f'<p class="web-citation"><span>[{number}]</span> '
                f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
                f'title="새 탭에서 출처 열기">{escape(title)}</a></p>')
    return re.sub(r'<p\b[^>]*>\[(\d+)\]\s+(.+?)\s+—\s+(https?://[^<\s]+)</p>',
                  replace, html)


_WEB_SOURCE = re.compile(
    r'<p class="web-citation"><span>\[(\d+)\]</span> <a href="([^"]+)" target="_blank" '
    r'rel="noopener noreferrer" title="[^"]*">(.*?)</a></p>')
_MARKER = re.compile(r"\[(\d{1,3})\]")


def inline_web_refs(html: str) -> str:
    """본문의 [n] 표시를 같은 답의 출처 n 과 같은 곳으로 가는 링크로 바꾼다.

    출처 목록은 web_links 가 이미 검사·이스케이프해 둔 줄에서만 읽는다 — 목록에 없는 번호는 글자 그대로 둔다.
    링크·코드 안과 출처 목록 줄 자체는 건드리지 않는다.
    """
    refs = {m[1]: (m[2], m[3]) for m in _WEB_SOURCE.finditer(html)}
    if not refs:
        return html

    def replace(match):
        ref = refs.get(str(int(match[1])))
        if not ref:
            return match[0]
        href, title = ref                      # 둘 다 이미 이스케이프된 값
        n = int(match[1])
        return (f'<a class="cite-ref" href="{href}" target="_blank" rel="noopener noreferrer" '
                f'title="출처 {n} · {title}"><span class="cite-bracket">[</span>{n}'
                f'<span class="cite-bracket">]</span></a>')

    parts = _TAG.split(html)
    blocked: list[str] = []
    for i, part in enumerate(parts):
        if part.startswith('<'):
            if part.startswith('<p class="web-citation"'):
                blocked.append('p')
                continue
            tag = re.match(r'<(/?)(a|pre|code|script|style|p)\b', part, re.I)
            if not tag:
                continue
            name = tag[2].lower()
            if tag[1]:
                if blocked and blocked[-1] == name:
                    blocked.pop()
            elif name != 'p':
                blocked.append(name)
        elif not blocked:
            parts[i] = _MARKER.sub(replace, part)
    return ''.join(parts)


def _targets(sources: list[dict]) -> list[tuple[str, str]]:
    """(문서 이름, 링크) — 긴 이름부터. 짧거나 중복된 이름은 링크하지 않는다."""
    seen: dict[str, str] = {}
    for s in sources or []:
        doc_id = s.get("doc_id")
        title = (s.get("title") or "").strip()
        if not doc_id or len(title) < 6 or title in seen:
            continue
        anchor = (s.get("heading") or "").strip() or title
        seen[title] = f"/doc/{doc_id}#q={quote(anchor)}"
    return sorted(seen.items(), key=lambda kv: -len(kv[0]))


def linkify(html: str, sources: list[dict]) -> str:
    """이미 렌더된 HTML 의 글자 부분에서만 문서 이름을 링크로 바꾼다."""
    html = inline_web_refs(document_links(web_links(html)))
    targets = _targets(sources)
    if not targets:
        return html
    parts = _TAG.split(html)
    in_link = False
    for i, part in enumerate(parts):
        if part.startswith("<"):
            if re.match(r'<a\b', part):
                in_link = True
            elif part.startswith('</a'):
                in_link = False
            continue                      # 태그 안은 건드리지 않는다
        if in_link:
            continue
        for title, href in targets:
            if title in part:
                part = part.replace(
                    title,
                    f'<a class="cite" href="{href}" title="근거 문서 열기">{title}</a>')
        parts[i] = part
    return "".join(parts)
