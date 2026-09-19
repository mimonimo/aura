"""HWP 5.0(바이너리) 구조 파서 — pyhwp `hwp5html` 변환본에서 문단·표·그림을 읽는다.

`hwp5txt`는 표를 `<표>` 자리표시로 접어 신청서·계획서처럼 표가 본문인 문서의
내용이 통째로 사라진다. `hwp5html`은 표를 `<table>`(rowspan/colspan, 셀 폭 mm)로,
그림을 `<img>`로 내보내므로 그 XHTML을 읽기 순서대로 걷는다. 표준 라이브러리
html.parser만 쓴다. 셀 격자화는 MinerU와 같은 html_table을 쓴다.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace as dc_replace
from html.parser import HTMLParser
from pathlib import Path

from zzaimy.ingest.parsers.base import (
    ParsedEntry,
    ParsedImage,
    ParsedPage,
    ParsedTable,
    ParseResult,
)
from zzaimy.ingest.parsers.html_table import parse_html_table

_WIDTH_RE = re.compile(r"width\s*:\s*([\d.]+)\s*(mm|pt|px)", re.IGNORECASE)
_NESTED_TABLE_TAG = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|td|th)\b[^>]*>", re.IGNORECASE)


class Hwp5NotInstalled(RuntimeError):
    pass


def _flatten_nested_tables(table_html: str) -> str:
    """바깥 표 HTML 안의 중첩 표 태그를 걷어낸다 — 중첩 표 글자는 바깥 셀 글자로 남는다."""
    out: list[str] = []
    depth = 0
    pos = 0
    for m in re.finditer(r"<table\b[^>]*>|</table\s*>", table_html, flags=re.IGNORECASE):
        seg = table_html[pos:m.start()]
        out.append(_NESTED_TABLE_TAG.sub(" ", seg) if depth >= 2 else seg)
        if m.group().lower().startswith("</"):
            if depth >= 2:
                out.append(" ")
            else:
                out.append(m.group())
            depth -= 1
        else:
            depth += 1
            out.append(" " if depth >= 2 else m.group())
        pos = m.end()
    out.append(table_html[pos:])
    return "".join(out)


def _col_widths(table_html: str, n_cols: int) -> tuple[float, ...]:
    """셀 style width(mm)로 열 폭 비율 — colspan 1인 셀만 쓴다. 못 채우면 빈 튜플."""

    class _W(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.row = -1
            self.col = 0
            self.occupied: set[tuple[int, int]] = set()
            self.widths: dict[int, float] = {}

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row += 1
                self.col = 0
            elif tag in ("td", "th"):
                a = dict(attrs)
                col = self.col
                while (self.row, col) in self.occupied:
                    col += 1
                rs = int(a.get("rowspan") or 1)
                cs = int(a.get("colspan") or 1)
                m = _WIDTH_RE.search(a.get("style") or "")
                if m and cs == 1:
                    self.widths.setdefault(col, float(m.group(1)))
                for dr in range(rs):
                    for dc in range(cs):
                        self.occupied.add((self.row + dr, col + dc))
                self.col = col + cs

    w = _W()
    w.feed(table_html)
    if n_cols and all(c in w.widths for c in range(n_cols)):
        total = sum(w.widths[c] for c in range(n_cols))
        if total > 0:
            return tuple(round(w.widths[c] / total, 4) for c in range(n_cols))
    return ()


class _Walker(HTMLParser):
    """XHTML을 읽기 순서로 걷는다 — 바깥 문단·표·그림만 항목으로 낸다."""

    def __init__(self, src: str, base_dir: Path) -> None:
        super().__init__()
        self.src = src
        self.base_dir = base_dir
        self.line_starts = [0]
        for i, ch in enumerate(src):
            if ch == "\n":
                self.line_starts.append(i + 1)
        self.table_depth = 0
        self.table_start = -1
        self.para: list[str] | None = None
        self.page_no = 1
        self.n_page_divs = 0
        # ("text", 문단) | ("table", 표 HTML 원문) | ("image", 그림 경로)
        self.items: list[tuple[str, object]] = []

    def _abs(self) -> int:
        line, off = self.getpos()
        return self.line_starts[line - 1] + off

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self.table_depth == 0:
                self._flush_para()
                self.table_start = self._abs()
            self.table_depth += 1
            return
        if tag == "img":
            src = a.get("src") or ""
            p = (self.base_dir / src).resolve() if src else None
            if p and p.exists() and p.is_file():
                if self.table_depth == 0:
                    self._flush_para()
                self.items.append(("image", p))
            return
        if self.table_depth:
            return
        if tag == "div" and "Page" in (a.get("class") or "").split():
            self.n_page_divs += 1
            self.page_no = self.n_page_divs
        elif tag == "p":
            self._flush_para()
            self.para = []
        elif tag == "br" and self.para is not None:
            self.para.append("\n")

    def handle_endtag(self, tag):
        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0 and self.table_start >= 0:
                end = self._abs() + len("</table>")
                self.items.append(("table", self.src[self.table_start:end]))
                self.table_start = -1
            return
        if self.table_depth:
            return
        if tag == "p":
            self._flush_para()

    def handle_data(self, data):
        if self.table_depth:
            return
        if self.para is not None:
            self.para.append(data)

    def _flush_para(self) -> None:
        if self.para is None:
            return
        text = "".join(self.para).replace("\r", "\n")
        self.para = None
        text = "\n".join(" ".join(ln.split()) for ln in text.splitlines())
        text = text.strip()
        if text:
            self.items.append(("text", text))


def parse_hwp5_html(src: str, base_dir: Path, parser_name: str = "hwp5") -> ParseResult:
    """hwp5html 산출 XHTML 문자열 → ParseResult (테스트에서 직접 쓴다)."""
    t0 = time.perf_counter()
    w = _Walker(src, base_dir)
    w.feed(src)
    w._flush_para()
    tables: list[ParsedTable] = []
    images: list[ParsedImage] = []
    entries: list[ParsedEntry] = []
    page_lines: list[str] = []
    page_no = 1
    for kind, payload in w.items:
        if kind == "text":
            text = str(payload)
            entries.append(ParsedEntry(page_no=page_no, kind="text", text=text))
            page_lines.append(text)
        elif kind == "table":
            raw = _flatten_nested_tables(str(payload))
            t = parse_html_table(raw, page_no=page_no)
            cells = tuple(
                dc_replace(
                    c, text="\n".join(
                        " ".join(ln.split())
                        for ln in html.unescape(c.text).replace("\r", "\n").splitlines()
                        if ln.strip()
                    ),
                )
                for c in t.cells
            )
            t = dc_replace(t, cells=cells, col_w=_col_widths(raw, t.n_cols))
            tables.append(t)
            entries.append(ParsedEntry(page_no=page_no, kind="table", ref=len(tables) - 1))
        elif kind == "image":
            images.append(ParsedImage(page_no=page_no, path=Path(str(payload))))
            entries.append(ParsedEntry(page_no=page_no, kind="image", ref=len(images) - 1))
            page_lines.append(f"[[img]]{Path(str(payload)).name}")
    pages = [ParsedPage(page_no=page_no, text="\n\n".join(page_lines))]
    return ParseResult(
        parser=parser_name, elapsed_s=time.perf_counter() - t0, pages=pages,
        tables=tables, images=images, entries=entries,
    )


class Hwp5Parser:
    name = "hwp5"

    def __init__(self, timeout_s: int = 300) -> None:
        self.timeout_s = timeout_s

    @staticmethod
    def _cli() -> str:
        beside = Path(sys.executable).parent / "hwp5html"
        if beside.exists():
            return str(beside)
        found = shutil.which("hwp5html")
        if found:
            return found
        raise Hwp5NotInstalled("hwp5html이 없다. pip install pyhwp")

    def parse(self, path: Path, work_dir: Path | None = None) -> ParseResult:
        cli = self._cli()
        out_dir = Path(work_dir) if work_dir else path.parent / f"{path.stem}_hwp5_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [cli, "--output", str(out_dir), str(path)],
            capture_output=True, text=True, timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"hwp5html 실패 (exit {proc.returncode}): {proc.stderr[-800:]}")
        html_file = next(
            (p for p in sorted(out_dir.glob("*.xhtml")) + sorted(out_dir.glob("*.html"))),
            None,
        )
        if html_file is None:
            raise RuntimeError(f"hwp5html 산출물이 {out_dir}에 없다")
        return parse_hwp5_html(
            html_file.read_text(encoding="utf-8", errors="replace"), out_dir, self.name
        )
