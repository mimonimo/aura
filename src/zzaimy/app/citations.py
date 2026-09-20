"""답변 안의 문서 이름을 그 문서로 가는 링크로 바꾼다.

왜: 에이전트가 "「영남이공대학교 산학협력단 운영 규정」 제4조에 따르면…" 이라고 답해도
담당자는 그 문서를 다시 찾아 들어가야 했다. 답변이 대는 근거는 눌러서 바로 갈 수 있어야 한다.

문서 이름은 근거 목록(sources)에 있는 것만 쓴다 — 모델이 지어낸 이름은 링크가 되지 않는다.
링크는 문서 화면의 문서 안 검색으로 이어져(#q=) 그 대목이 바로 보인다.
"""
from __future__ import annotations

import re
from urllib.parse import quote

_TAG = re.compile(r"(<[^>]+>)")


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
    targets = _targets(sources)
    if not targets:
        return html
    parts = _TAG.split(html)
    for i, part in enumerate(parts):
        if part.startswith("<"):
            continue                      # 태그 안은 건드리지 않는다
        for title, href in targets:
            if title in part:
                part = part.replace(
                    title,
                    f'<a class="cite" href="{href}" title="근거 문서 열기">{title}</a>')
        parts[i] = part
    return "".join(parts)
