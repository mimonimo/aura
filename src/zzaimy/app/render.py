"""추출 결과 렌더링 — doc_chunks를 문서 모양의 HTML 블록으로 바꾼다.

표 조각은 JSON 구조({n_rows, n_cols, cells})로 저장되며, 병합 셀(rowspan/colspan)과
머리글(th)을 유지한 채 실제 표로 그린다. 셀 내용은 전부 이스케이프한다.
"""

from __future__ import annotations

import json
from collections import defaultdict

from markupsafe import Markup, escape


def table_html(content: str) -> Markup:
    try:
        data = json.loads(content)
        cells = data["cells"]
        n_rows = int(data["n_rows"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # 구조를 못 읽으면 원문 그대로 (구버전 조각 호환)
        return Markup('<pre class="doc-text">{}</pre>').format(content)

    rows: dict[int, list] = defaultdict(list)
    for r, c, rs, cs, hd, txt in cells:
        rows[int(r)].append((int(c), int(rs), int(cs), bool(hd), str(txt)))
    parts = ['<div class="table-scroll"><table class="extract">']
    for r in range(n_rows):
        parts.append("<tr>")
        for c, rs, cs, hd, txt in sorted(rows.get(r, [])):
            tag = "th" if hd else "td"
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (
                f' colspan="{cs}"' if cs > 1 else ""
            )
            parts.append(f"<{tag}{attrs}>{escape(txt)}</{tag}>")
        parts.append("</tr>")
    parts.append("</table></div>")
    return Markup("".join(parts))


def chunk_blocks(chunks: list[dict]) -> list[Markup]:
    """조각 목록을 순서대로 HTML 블록으로 — 본문 문단과 표가 원래 순서로 흐른다."""
    blocks: list[Markup] = []
    for c in chunks:
        if c["kind"] == "table":
            blocks.append(table_html(c["content"]))
        elif c["kind"] == "heading":
            blocks.append(Markup('<h4 class="extract-h">{}</h4>').format(c["content"]))
        else:
            blocks.append(Markup('<p class="extract-p">{}</p>').format(c["content"]))
    return blocks
