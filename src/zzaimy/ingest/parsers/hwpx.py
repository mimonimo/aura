"""HWPX(OWPML) 구조 파서 — 문단·표(병합 셀)·그림·캡션을 읽기 순서로.

HWPX는 ZIP 안의 `Contents/section*.xml`(OWPML)이다. 표는 `hp:tbl` → `hp:tr` →
`hp:tc`(셀 주소 `hp:cellAddr`, 병합 `hp:cellSpan`, 머리글 `header` 속성), 그림은
`hp:pic` 안의 `hc:img binaryItemIDRef`가 `Contents/content.hpf`의 항목을 가리킨다.
표·그림의 `hp:caption`은 캡션으로 읽는다. 태그 접두어(네임스페이스)는 문서마다
다를 수 있어 local-name으로만 본다. 표준 라이브러리만 쓴다.

태그를 모두 걷어내던 이전 방식은 표 셀이 줄글로 흩어져 구조가 사라졌다 —
신청서·계획서처럼 표가 본문인 문서에서 어느 칸의 값인지 알 수 없었다.
"""

from __future__ import annotations

import dataclasses
import re
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from zzaimy.ingest.parsers.base import (
    ParsedEntry,
    ParsedImage,
    ParsedPage,
    ParsedTable,
    ParseResult,
    TableCell,
)

try:  # XML 폭탄·외부 개체 방어 — 있으면 쓰고, 없으면 표준 파서
    from defusedxml.ElementTree import fromstring as _fromstring
except Exception:  # pragma: no cover - 선택 의존성
    from xml.etree.ElementTree import fromstring as _fromstring

_SECTION_RE = re.compile(r"Contents/section(\d+)\.xml$")
# 문단 스타일 이름으로 제목을 알아본다 — 한글 기본 스타일('개요 1~7', '제목')과 영문명
_HEADING_STYLE_RE = re.compile(r"개요|제목|heading|title", re.IGNORECASE)


class EncryptedHwpxError(ValueError):
    """배포용(암호화) HWPX — 본문 섹션이 암호화돼 키 없이 추출 불가."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.split(":")[-1]


def _text_of(el: ET.Element) -> str:
    """요소 아래 `hp:t`의 글자만 잇는다 (표·그림 안의 글자는 제외)."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        for ch in node:
            name = _local(ch.tag)
            if name == "t":
                parts.append("".join(ch.itertext()))
            elif name in ("tbl", "pic", "secPr", "ctrl"):
                continue
            else:
                walk(ch)

    walk(el)
    return "".join(parts)


def _sublist_text(el: ET.Element) -> str:
    """`hp:subList`(셀·캡션 본문) → 문단마다 한 줄. 중첩 표는 글자만 이어 붙인다."""
    lines: list[str] = []
    for node in el.iter():
        if _local(node.tag) != "p":
            continue
        # 중첩 표 안의 문단은 바깥 셀 문단 순회에서 다시 나오므로 여기서는 직접 글자만
        text = " ".join("".join(t.itertext()) for t in node.iter() if _local(t.tag) == "t")
        text = " ".join(text.split())
        if text:
            lines.append(text)
    # iter()는 중첩 표의 문단도 포함하므로 같은 줄이 두 번 나올 수 있다 — 순서 유지 중복 제거
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return "\n".join(out)


def _caption_text(el: ET.Element) -> str:
    for ch in el:
        if _local(ch.tag) == "caption":
            return _sublist_text(ch)
    return ""


def _parse_table(tbl: ET.Element, page_no: int) -> ParsedTable:
    cells: list[TableCell] = []
    widths: dict[int, float] = {}
    for tr in (ch for ch in tbl if _local(ch.tag) == "tr"):
        for tc in (ch for ch in tr if _local(ch.tag) == "tc"):
            row = col = 0
            rs = cs = 1
            width = 0.0
            sub: ET.Element | None = None
            for part in tc:
                name = _local(part.tag)
                if name == "cellAddr":
                    row = int(part.get("rowAddr") or 0)
                    col = int(part.get("colAddr") or 0)
                elif name == "cellSpan":
                    rs = max(int(part.get("rowSpan") or 1), 1)
                    cs = max(int(part.get("colSpan") or 1), 1)
                elif name == "cellSz":
                    width = float(part.get("width") or 0)
                elif name == "subList":
                    sub = part
            text = _sublist_text(sub) if sub is not None else ""
            header = str(tc.get("header") or "0").lower() in ("1", "true")
            cells.append(TableCell(
                row=row, col=col, text=text, row_span=rs, col_span=cs, is_header=header,
            ))
            if cs == 1 and width > 0:
                widths.setdefault(col, width)
    cells.sort(key=lambda c: (c.row, c.col))
    n_rows = max((c.row + c.row_span for c in cells), default=0)
    n_cols = max((c.col + c.col_span for c in cells), default=0)
    n_rows = max(n_rows, int(tbl.get("rowCnt") or 0))
    n_cols = max(n_cols, int(tbl.get("colCnt") or 0))
    col_w: tuple[float, ...] = ()
    if n_cols and all(c in widths for c in range(n_cols)):
        total = sum(widths[c] for c in range(n_cols))
        if total > 0:
            col_w = tuple(round(widths[c] / total, 4) for c in range(n_cols))
    return ParsedTable(
        page_no=page_no, n_rows=n_rows, n_cols=n_cols, cells=tuple(cells), col_w=col_w,
    )


def _binary_map(zf: zipfile.ZipFile, names: list[str]) -> dict[str, str]:
    """content.hpf의 항목 id → ZIP 안 경로. 없으면 BinData/ 파일명 어간으로 맞춘다."""
    out: dict[str, str] = {}
    hpf = next((n for n in names if n.endswith("content.hpf")), None)
    if hpf:
        try:
            root = _fromstring(zf.read(hpf))
            for item in root.iter():
                if _local(item.tag) == "item" and item.get("id") and item.get("href"):
                    out[str(item.get("id"))] = str(item.get("href")).lstrip("/")
        except ET.ParseError:
            pass
    for n in names:
        if n.startswith("BinData/"):
            out.setdefault(Path(n).stem, n)
    return out


def _heading_styles(zf: zipfile.ZipFile, names: list[str]) -> set[str]:
    """header.xml의 문단 스타일 중 제목 계열의 id 집합 (없으면 빈 집합)."""
    header = next((n for n in names if n.endswith("Contents/header.xml")), None)
    if not header:
        return set()
    try:
        root = _fromstring(zf.read(header))
    except ET.ParseError:
        return set()
    ids: set[str] = set()
    for st in root.iter():
        if _local(st.tag) != "style":
            continue
        name = f"{st.get('name') or ''} {st.get('engName') or ''}"
        if st.get("id") is not None and _HEADING_STYLE_RE.search(name):
            ids.add(str(st.get("id")))
    return ids


# 번호로 시작하는 제목 꼴 — Ⅰ. / 1. / 1.1. / 가. / (1) / 1) (chunk_path·section_context 와 같은 갈래). □·○ 같은 개조식 부호는 본문 항목이다
_NUMBERED_HEAD = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]|\d+(?:\.\d+)*[.．]?(?=\s)|[가-하][.．]|\(\d+\)|\d+\))\s*\S")
HEADING_MAX_CHARS = 60
_DATE_LIKE = re.compile(r"^\s*(?:19|20)\d{2}\s*[.．]\s*\d{0,2}\s*[.．]?\s*(?:\d{1,2}\s*[.．]?)?\s*$")   # 표지 날짜 '2026. 2.'
_TRAILING_PAGE = re.compile(r"^(.*?\S)\s*\d{1,4}\s*$")


def demote_toc_lines(entries: list) -> int:
    """목차 쪽의 줄 — 끝에 쪽 번호가 붙은 제목인데 번호를 뗀 글이 뒤에서 제목으로 다시 나오면 목차 줄이다(본문 제목과 두 번 잡혔다,
    실측 2026-10-01 LINC3.0 3차년도 계획서). 고친 개수를 돌려준다."""
    heads = [(i, e.text) for i, e in enumerate(entries) if e.kind == "heading"]
    later: dict[str, int] = {}
    for i, t in heads:
        later[" ".join(t.split())] = i                                  # 같은 글의 마지막 위치
    n = 0
    for i, t in heads:
        m = _TRAILING_PAGE.match(t)
        if not m:
            continue
        base = " ".join(m.group(1).split())
        if later.get(base, -1) > i:
            entries[i] = dataclasses.replace(entries[i], kind="text")
            n += 1
    return n


def _char_shapes(zf: zipfile.ZipFile, names: list[str]) -> tuple[dict[str, tuple[int, bool]], set[str], int]:
    """글자 모양(id → (크기, 굵게)), 개요 문단 모양 id, 바탕글 글자 크기."""
    header = next((n for n in names if n.endswith("Contents/header.xml")), None)
    if not header:
        return {}, set(), 1000
    try:
        root = _fromstring(zf.read(header))
    except ET.ParseError:
        return {}, set(), 1000
    chars: dict[str, tuple[int, bool]] = {}
    outline: set[str] = set()
    body_cp = "0"
    for el in root.iter():
        name = _local(el.tag)
        if name == "charPr" and el.get("id") is not None:
            bold = any(_local(c.tag) == "bold" for c in el)
            try:
                chars[str(el.get("id"))] = (int(el.get("height") or 1000), bold)
            except ValueError:
                chars[str(el.get("id"))] = (1000, bold)
        elif name == "paraPr" and el.get("id") is not None:
            if any(_local(c.tag) == "heading" and (c.get("type") or "").upper() == "OUTLINE" for c in el):
                outline.add(str(el.get("id")))
        elif name == "style" and str(el.get("id")) == "0":
            body_cp = str(el.get("charPrIDRef") or "0")
    return chars, outline, chars.get(body_cp, (1000, False))[0]


def is_heading(text: str, char: tuple[int, bool] | None, outline: bool, body_height: int) -> bool:
    """문단이 절 제목인가 — 개요 문단이거나, 짧고 번호로 시작하며 본문보다 크거나 굵은 글자(2026-10-01, ADR-0048).
    실물 서식은 개요 스타일을 거의 쓰지 않고 번호를 글자로 쳐서 제목을 만든다(557 서식: 스타일로 찾은 제목 4개)."""
    if outline:
        return True
    t = (text or "").strip()
    if not t or len(t) > HEADING_MAX_CHARS or not _NUMBERED_HEAD.match(t) or _DATE_LIKE.match(t):
        return False
    height, bold = char or (body_height, False)
    return bold or height >= body_height + 100


class HwpxParser:
    name = "hwpx"

    def parse(self, path: Path, work_dir: Path | None = None) -> ParseResult:
        """work_dir에 그림을 풀어 둔다 (기본: 문서 옆 `<이름>_imgs`)."""
        t0 = time.perf_counter()
        path = Path(path)
        img_dir = Path(work_dir) if work_dir else path.parent / f"{path.stem}_imgs"
        pages: list[ParsedPage] = []
        tables: list[ParsedTable] = []
        images: list[ParsedImage] = []
        entries: list[ParsedEntry] = []
        warnings: list[str] = []
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            sections = sorted(
                (n for n in names if _SECTION_RE.search(n)),
                key=lambda n: int(_SECTION_RE.search(n).group(1)),  # type: ignore[union-attr]
            )
            bin_map = _binary_map(zf, names)
            heading_ids = _heading_styles(zf, names)
            char_shapes, outline_ids, body_height = _char_shapes(zf, names)
            extracted: dict[str, Path] = {}

            def image_for(el: ET.Element, page_no: int) -> None:
                ref = ""
                for node in el.iter():
                    if _local(node.tag) == "img" and node.get("binaryItemIDRef"):
                        ref = node.get("binaryItemIDRef") or ""
                        break
                member = bin_map.get(ref) or bin_map.get(Path(ref).stem)
                if not member or member not in names:
                    return
                if member not in extracted:
                    img_dir.mkdir(parents=True, exist_ok=True)
                    dest = img_dir / Path(member).name
                    try:
                        dest.write_bytes(zf.read(member))
                    except (KeyError, OSError):
                        return
                    extracted[member] = dest
                images.append(ParsedImage(page_no=page_no, path=extracted[member]))
                entries.append(ParsedEntry(
                    page_no=page_no, kind="image", ref=len(images) - 1,
                    text=_caption_text(el),
                ))

            for sec_idx, sec in enumerate(sections, start=1):
                raw = zf.read(sec)
                if not raw.lstrip().startswith(b"<"):
                    raise EncryptedHwpxError(f"배포용/암호화 HWPX로 보임: {path.name}")
                root = _fromstring(raw)
                page_lines: list[str] = []
                for para in (ch for ch in root if _local(ch.tag) == "p"):
                    styled = str(para.get("styleIDRef")) in heading_ids
                    outline_para = str(para.get("paraPrIDRef")) in outline_ids
                    first_cp = next((str(r.get("charPrIDRef")) for r in para if _local(r.tag) == "run"
                                     and any(_local(o.tag) == "t" and "".join(o.itertext()).strip() for o in r)), None)
                    buf: list[str] = []

                    def flush() -> None:
                        text = " ".join("".join(buf).split())
                        buf.clear()
                        if text:
                            head = styled or is_heading(text, char_shapes.get(first_cp or ""), outline_para, body_height)
                            entries.append(ParsedEntry(page_no=sec_idx, kind="heading" if head else "text", text=text))
                            page_lines.append(text)

                    for run in (ch for ch in para if _local(ch.tag) == "run"):
                        for obj in run:
                            name = _local(obj.tag)
                            if name == "t":
                                buf.append("".join(obj.itertext()))
                            elif name == "tbl":
                                flush()
                                tables.append(_parse_table(obj, sec_idx))
                                entries.append(ParsedEntry(
                                    page_no=sec_idx, kind="table", ref=len(tables) - 1,
                                    text=_caption_text(obj),
                                ))
                            elif name == "pic":
                                flush()
                                image_for(obj, sec_idx)
                            elif name in ("secPr", "ctrl"):
                                continue
                            else:
                                # 도형·글상자 등 — 안의 글자만 살린다
                                inner = _text_of(obj)
                                if inner.strip():
                                    buf.append(inner)
                                for node in obj.iter():
                                    if _local(node.tag) == "tbl":
                                        flush()
                                        tables.append(_parse_table(node, sec_idx))
                                        entries.append(ParsedEntry(
                                            page_no=sec_idx, kind="table",
                                            ref=len(tables) - 1, text=_caption_text(node),
                                        ))
                    flush()
                pages.append(ParsedPage(page_no=sec_idx, text="\n\n".join(page_lines)))
        toc = demote_toc_lines(entries)
        if toc:
            warnings.append(f"목차 줄 {toc}개는 제목에서 뺐다")
        return ParseResult(
            parser=self.name, elapsed_s=time.perf_counter() - t0, pages=pages,
            tables=tables, images=images, entries=entries, warnings=warnings,
        )
