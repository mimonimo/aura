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


def table_csv(content: str) -> str:
    """표 JSON → CSV (엑셀 호환, 병합 셀은 좌상단 셀에만 값)."""
    import csv
    import io

    data = json.loads(content)
    grid = [["" for _ in range(int(data["n_cols"]))] for _ in range(int(data["n_rows"]))]
    for r, c, _rs, _cs, _hd, txt in data["cells"]:
        if int(r) < len(grid) and int(c) < len(grid[0]):
            grid[int(r)][int(c)] = str(txt)
    buf = io.StringIO()
    csv.writer(buf).writerows(grid)
    return buf.getvalue()


def export_markdown(filename: str, chunks: list[dict]) -> str:
    """추출 결과 전체를 마크다운으로 — 소제목·문단·표(파이프 그리드)."""
    lines = [f"# {filename} — 추출 결과", ""]
    for c in chunks:
        if c["kind"] == "heading":
            lines += [f"### {c['content']}", ""]
        elif c["kind"] == "table":
            try:
                data = json.loads(c["content"])
            except (json.JSONDecodeError, TypeError):
                lines += [c["content"], ""]
                continue
            grid = [
                ["" for _ in range(int(data["n_cols"]))]
                for _ in range(int(data["n_rows"]))
            ]
            for r, col, _rs, _cs, _hd, txt in data["cells"]:
                if int(r) < len(grid) and int(col) < len(grid[0]):
                    grid[int(r)][int(col)] = str(txt).replace("\n", " ")
            if grid:
                lines.append("| " + " | ".join(grid[0]) + " |")
                lines.append("|" + "---|" * len(grid[0]))
                for row in grid[1:]:
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")
        else:
            lines += [c["content"], ""]
    return "\n".join(lines)


def chunk_blocks(
    chunks: list[dict],
    doc_id: int | None = None,
    asset_by_name: dict[str, int] | None = None,
) -> list[Markup]:
    """조각 목록을 순서대로 HTML 블록으로 — 문단·표·그림이 원래 순서·페이지대로 흐른다."""
    blocks: list[Markup] = []
    last_page: int | None = None
    for c in chunks:
        pg = c.get("page_no")
        if pg and pg != last_page:
            if last_page is not None:
                blocks.append(
                    Markup('<div class="extract-page">{}쪽</div>').format(pg)
                )
            last_page = pg
        if c["kind"] == "image":
            aid = (asset_by_name or {}).get(c["content"])
            if doc_id is not None and aid:
                blocks.append(Markup(
                    '<figure style="margin:6px 0 16px;">'
                    '<img src="/doc/{d}/asset/{a}" style="max-width:70%; border:1px solid'
                    ' var(--line); border-radius:10px; display:block;">'
                    '<figcaption class="muted" style="font-size:11px; margin-top:3px;">'
                    '추출 그림 · <a href="/doc/{d}/asset/{a}?dl=1">내려받기</a>'
                    '</figcaption></figure>'
                ).format(d=doc_id, a=aid))
            continue
        if c["kind"] == "table":
            block = table_html(c["content"])
            if doc_id is not None and c.get("id"):
                block += Markup(
                    '<p style="margin:-10px 0 14px; text-align:right;">'
                    '<a href="/doc/{}/table/{}.csv" class="muted"'
                    ' style="font-size:11.5px;">표 CSV 내려받기</a></p>'
                ).format(doc_id, c["id"])
            blocks.append(block)
        elif c["kind"] == "heading":
            blocks.append(Markup('<h4 class="extract-h">{}</h4>').format(c["content"]))
        else:
            blocks.append(Markup('<p class="extract-p">{}</p>').format(c["content"]))
    return blocks
