"""추출 결과 렌더링 — doc_chunks를 문서 모양의 HTML 블록으로 바꾼다.

표 조각은 JSON 구조({n_rows, n_cols, cells})로 저장되며, 병합 셀(rowspan/colspan)과
머리글(th)을 유지한 채 실제 표로 그린다. 셀 내용은 전부 이스케이프한다.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from markupsafe import Markup, escape


def _rich(text: str) -> Markup:
    """이스케이프 후 **굵게**만 살린다 — 비전 판독의 강조 보존."""
    escaped = str(escape(text))
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped))


_TOC_LINE = re.compile(r"[·.…]{3,}\s*\d{1,3}")


def _toc_html(rows: dict[int, list], n_rows: int) -> Markup:
    """목차 표 → 항목·쪽번호 목록. 2단 목차는 열 우선(왼쪽 열 먼저)으로 읽는다."""
    cols: dict[int, list[str]] = defaultdict(list)
    for r in range(n_rows):
        for c, _rs, _cs, _hd, txt in sorted(rows.get(r, [])):
            if str(txt).strip():
                cols[c].append(str(txt))
    items: list[tuple[str, str]] = []
    for c in sorted(cols):
        for cell in cols[c]:
            # 셀 안에 여러 항목이 붙어 있으면 '제목 ···· 쪽수' 단위로 자른다
            for m in re.finditer(r"(.+?)[·.…]{3,}\s*(\d{1,3})", cell):
                title = m.group(1).strip(" ·.…-")
                if title:
                    items.append((title, m.group(2)))
    if not items:
        return Markup('<pre class="doc-text">{}</pre>').format(
            "\n".join(t for col in cols.values() for t in col)
        )
    parts = ['<div class="extract-toc"><div class="extract-toc-title">목차</div>']
    for title, page in items:
        parts.append(
            f'<div class="toc-row"><span class="toc-t">{escape(title)}</span>'
            f'<span class="toc-p">{escape(page)}</span></div>'
        )
    parts.append("</div>")
    return Markup("".join(parts))


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

    # 목차 표 감지 — 점선 리더(····)+쪽번호 셀이 많으면 표 대신 목차로 그린다
    all_texts = [str(t) for *_, t in cells if str(t).strip()]
    n_toc = sum(1 for t in all_texts if _TOC_LINE.search(t))
    if all_texts and n_toc * 2 >= len(all_texts):
        return _toc_html(rows, int(data["n_rows"]))

    col_w = data.get("col_w") or []
    if col_w:
        # 괘선 직독의 원본 열 폭 비율 — 고정 레이아웃이어야 비율이 지켜진다
        parts = [
            '<div class="table-scroll">'
            '<table class="extract" style="table-layout:fixed; width:100%;">'
            "<colgroup>"
        ]
        for w in col_w:
            parts.append(f'<col style="width:{w * 100:.2f}%;">')
        parts.append("</colgroup>")
    else:
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


def table_grid(data: dict, fill_spans: bool = False) -> list[list[str]]:
    """표 JSON → 2차원 문자열 격자. fill_spans면 병합 범위 전체에 같은 값을 채운다."""
    n_rows, n_cols = int(data["n_rows"]), int(data["n_cols"])
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for r, c, rs, cs, _hd, txt in data["cells"]:
        r, c, rs, cs = int(r), int(c), int(rs), int(cs)
        if not (0 <= r < n_rows and 0 <= c < n_cols):
            continue
        t = " ".join(str(txt).split())
        if not fill_spans:
            grid[r][c] = t
            continue
        for dr in range(max(rs, 1)):
            for dc in range(max(cs, 1)):
                if r + dr < n_rows and c + dc < n_cols:
                    grid[r + dr][c + dc] = t
    return grid


def render_table_text(data: dict, max_chars: int = 6000) -> str:
    """표 JSON(dict) → 검색·인용용 평문 — 캡션 줄, 행마다 ' | '로 이은 셀, 각주 줄.

    병합 셀 값은 병합 범위 전체에 채워 각 행·열이 머리글 맥락을 잃지 않게 한다
    (단위·연도 머리글이 붙어야 수치가 근거로 쓰인다). 화면 표는 table_html이 그린다.
    """
    lines: list[str] = []
    caption = " ".join(str(data.get("caption") or "").split())
    if caption:
        lines.append(caption)
    for row in table_grid(data, fill_spans=True):
        if any(v for v in row):
            lines.append(" | ".join(row))
    note = " ".join(str(data.get("note") or "").split())
    if note:
        lines.append(note)
    return "\n".join(lines)[:max_chars]


def table_text(content: str) -> str:
    """표 조각 content → 평문. 저장된 text가 있으면 그것, 없으면 셀에서 만든다.

    JSON이 아니면(구버전·파이프 조각) 원문 그대로 돌려준다.
    """
    try:
        data = json.loads(content)
        data["cells"]
        int(data["n_rows"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return content
    stored = data.get("text")
    if isinstance(stored, str) and stored.strip():
        return stored
    return render_table_text(data)


def table_context(content: str) -> tuple[str, str]:
    """표 조각의 (캡션, 각주) — 없으면 빈 문자열."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return (
        " ".join(str(data.get("caption") or "").split()),
        " ".join(str(data.get("note") or "").split()),
    )


def export_markdown(filename: str, chunks: list[dict]) -> str:
    """추출 결과 전체를 마크다운으로 — 소제목·문단·표(캡션+파이프 그리드)·그림 글자."""
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
            caption, note = table_context(c["content"])
            if caption:
                lines += [f"**{caption}**", ""]
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
            if note:
                lines += [note, ""]
        elif c["kind"] == "image_text":
            # 그림 캡션·그림 속 글자 — 인용부로 표시해 본문과 구분한다
            lines += ["> " + c["content"].replace("\n", "\n> "), ""]
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
    prev_kind = ""
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
            prev_kind = "image"
            continue
        if c["kind"] == "image_text":
            # 그림 캡션·그림 속 글자(OCR) — 바로 앞 그림 아래에 작은 글씨로
            style = (
                "font-size:12px; line-height:1.55; white-space:pre-wrap;"
                + (" margin:-10px 0 16px;" if prev_kind == "image" else " margin:0 0 12px;")
            )
            blocks.append(Markup(
                '<div class="extract-figtext muted" style="{}">'
                '<span style="font-weight:700;">그림 설명·글자</span> {}</div>'
            ).format(Markup(style), _rich(c["content"])))
            prev_kind = "image_text"
            continue
        prev_kind = c["kind"]
        if c["kind"] == "table":
            caption, note = table_context(c["content"])
            block = Markup("")
            if caption:
                block += Markup(
                    '<div class="extract-caption" style="font-size:13px; font-weight:700;'
                    ' color:var(--navy); margin:8px 0 4px;">{}</div>'
                ).format(caption)
            block += table_html(c["content"])
            if note:
                block += Markup(
                    '<div class="extract-note muted" style="font-size:12px;'
                    ' margin:-10px 0 12px;">{}</div>'
                ).format(note)
            if doc_id is not None and c.get("id"):
                block += Markup(
                    '<p style="margin:-10px 0 14px; text-align:right;">'
                    '<a href="/doc/{}/table/{}.csv" class="muted"'
                    ' style="font-size:11.5px;">표 CSV 내려받기</a></p>'
                ).format(doc_id, c["id"])
            blocks.append(block)
        elif c["kind"] == "heading":
            blocks.append(Markup('<h4 class="extract-h">{}</h4>').format(_rich(c["content"])))
        else:
            blocks.append(Markup('<p class="extract-p">{}</p>').format(_rich(c["content"])))
    return blocks


def layout_pages(*_args: object, **_kwargs: object) -> None:
    """좌표 기반 재현은 쓰지 않는다 — 문서 보기는 글자층 PDF 뷰어가 맡는다.

    원본 쪽 그림 위에 글자층만 얹은 PDF를 브라우저 내장 뷰어로 띄우므로
    배치·비율·글자 크기를 웹에서 다시 맞출 일이 없다. 아직 이 이름을 부르는
    호출부(main.py)가 있어 None만 돌려 흐름 보기로 물러나게 한다.
    """
    return None


def trailing_image_blocks(doc_id: int, assets: list[dict]) -> list[Markup]:
    """위치 정보가 없는 추출 그림을 프리뷰 말미 섹션으로 — 복원 문서와 동일 구성."""
    if not assets:
        return []
    blocks = [Markup('<h4 class="extract-h">추출 그림</h4>')]
    for a in assets:
        blocks.append(Markup(
            '<figure style="margin:6px 0 16px;">'
            '<img src="/doc/{d}/asset/{a}" style="max-width:70%; border:1px solid'
            ' var(--line); border-radius:10px; display:block;">'
            '<figcaption class="muted" style="font-size:11px; margin-top:3px;">'
            'p{p} · <a href="/doc/{d}/asset/{a}?dl=1">내려받기</a>'
            '</figcaption></figure>'
        ).format(d=doc_id, a=a["id"], p=a.get("page_no") or "?"))
    return blocks


def build_docx(
    filename: str,
    chunks: list[dict],
    asset_paths: dict[str, str] | None = None,
    extra_images: list[str] | None = None,
) -> bytes:
    """추출 조각을 편집 가능한 워드 문서로 복원 — 제목·문단·병합 표·그림.

    전자 문서 복원이 이 파이프라인의 최종 목표다 (ADR-0007).
    """
    import io

    from docx import Document
    from docx.shared import Inches, Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)

    for c in chunks:
        kind = c["kind"]
        if kind == "heading":
            doc.add_heading(c["content"], level=2)
        elif kind == "table":
            try:
                data = json.loads(c["content"])
                n_rows, n_cols = int(data["n_rows"]), int(data["n_cols"])
                if n_rows < 1 or n_cols < 1:
                    continue
                caption, note = table_context(c["content"])
                if caption:
                    doc.add_paragraph(caption).runs[0].bold = True
                t = doc.add_table(rows=n_rows, cols=n_cols)
                t.style = "Table Grid"
                col_w = data.get("col_w") or []
                if len(col_w) == n_cols:
                    # 괘선 직독의 원본 열 폭 비율 → 실제 열 너비 (A4 본문 6.3in)
                    t.autofit = False
                    for ci, w in enumerate(col_w):
                        width = Inches(6.3 * float(w))
                        for row in t.rows:
                            row.cells[ci].width = width
                for r, col, rs, cs, hd, txt in data["cells"]:
                    r, col, rs, cs = int(r), int(col), int(rs), int(cs)
                    if r >= n_rows or col >= n_cols:
                        continue
                    cell = t.cell(r, col)
                    end_r = min(r + rs - 1, n_rows - 1)
                    end_c = min(col + cs - 1, n_cols - 1)
                    if (end_r, end_c) != (r, col):
                        cell = cell.merge(t.cell(end_r, end_c))
                    cell.text = str(txt)
                    if hd:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                run.bold = True
                doc.add_paragraph(note if note else "")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                doc.add_paragraph(c["content"])
        elif kind == "image":
            path = (asset_paths or {}).get(c["content"])
            if path:
                try:
                    doc.add_picture(path, width=Inches(5.2))
                except Exception:
                    pass
        else:
            # **굵게** 강조를 워드 굵기로 옮긴다
            para = doc.add_paragraph()
            for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", c["content"])):
                run = para.add_run(part)
                run.bold = i % 2 == 1

    # 본문에 그림 위치가 없는 경우(비전 전사 등) — 추출 그림을 끝에 첨부한다
    placed = any(c["kind"] == "image" for c in chunks)
    if not placed and extra_images:
        doc.add_heading("추출 그림", level=2)
        for path in extra_images[:20]:
            try:
                doc.add_picture(path, width=Inches(5.2))
            except Exception:
                continue

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
