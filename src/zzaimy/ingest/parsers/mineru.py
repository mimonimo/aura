"""MinerU 어댑터 (W1-W2 TASK-04).

MinerU는 파이썬 API가 버전마다 바뀌어 CLI(`mineru -p .. -o .. -b pipeline`)를
서브프로세스로 호출하고 산출물 `*_content_list.json`을 정규화한다.
표는 HTML(`table_body`)로 나오므로 html_table로 그리드화한다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from dataclasses import replace as _dc_replace

from zzaimy.ingest.parsers.base import (
    ParsedEntry,
    ParsedImage,
    ParsedPage,
    ParsedTable,
    ParseResult,
)
from zzaimy.ingest.parsers.html_table import parse_html_table
from zzaimy.ingest.parsers.ocr_table import derive_col_widths


def extract_middle_lines(
    middle: dict, min_score: float = 0.5
) -> tuple[list[dict], dict[int, tuple[float, float]]]:
    """MinerU middle.json에서 줄 단위 OCR 좌표를 뽑는다.

    스캔 문서의 복원 뷰(원본 배치 투명 레이어)와 검색 가능 PDF의 재료.
    좌표는 middle.json의 page_size 좌표계(top 기준) 그대로 둔다.
    """
    lines: list[dict] = []
    sizes: dict[int, tuple[float, float]] = {}
    for info in middle.get("pdf_info") or []:
        page_no = int(info.get("page_idx", 0)) + 1
        ps = info.get("page_size") or []
        if len(ps) == 2:
            sizes[page_no] = (float(ps[0]), float(ps[1]))
        for block in info.get("preproc_blocks") or []:
            for ln in block.get("lines") or []:
                bb = ln.get("bbox")
                if not (isinstance(bb, (list, tuple)) and len(bb) == 4):
                    continue
                spans = [
                    sp for sp in ln.get("spans") or []
                    if sp.get("type") == "text"
                    and str(sp.get("content") or "").strip()
                    and float(sp.get("score") or 1.0) >= min_score
                ]
                if not spans:
                    continue
                text = " ".join(str(sp["content"]).strip() for sp in spans)
                score = min(float(sp.get("score") or 1.0) for sp in spans)
                lines.append({
                    "page_no": page_no,
                    "kind": "text",
                    "content": text,
                    "bbox": ",".join(f"{float(v):.1f}" for v in bb),
                    "score": round(score, 3),
                })
    return lines, sizes


def _caption_lines(*groups) -> str:
    """MinerU 캡션·각주 목록(들) → 줄 단위 문자열. 첫 줄이 캡션, 다음 줄들이 각주."""
    lines: list[str] = []
    for g in groups:
        if isinstance(g, str):
            g = [g]
        for x in g or []:
            t = " ".join(str(x).split())
            if t:
                lines.append(t)
    return "\n".join(lines)


def _parse_bbox_str(s: object) -> tuple[float, float, float, float] | None:
    """ocr_lines의 'x0,y0,x1,y1' 문자열 → 좌표 튜플. 어긋나면 None."""
    try:
        x0, y0, x1, y1 = (float(v) for v in str(s).split(","))
        return x0, y0, x1, y1
    except (TypeError, ValueError):
        return None


def fill_ocr_col_widths(
    tables: list[ParsedTable],
    entries: list[ParsedEntry],
    ocr_lines: list[dict],
) -> list[ParsedTable]:
    """괘선 없는 스캔·사진 표의 열 폭 비율을 OCR 글자줄 좌표에서 인출해 채운다.

    표 영역(entry.bbox) 안에 놓인 글자줄들의 x 분포로 열 경계를 찾는다
    (ocr_table.derive_col_widths). 이미 col_w가 있으면(디지털 PDF 괘선·HWP
    원본 폭) 건드리지 않고, 좌표 신뢰도가 낮으면 비운 채 둔다(균등 폭 폴백).
    좌표계가 어긋나 표 안에 잡히는 줄이 없으면 자연히 폴백한다(fail-closed).
    """
    by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for ln in ocr_lines:
        bb = _parse_bbox_str(ln.get("bbox"))
        if bb is not None:
            by_page.setdefault(int(ln.get("page_no", 0) or 0), []).append(bb)
    if not by_page:
        return tables

    new = list(tables)
    for e in entries:
        if getattr(e, "kind", "") != "table" or e.bbox is None:
            continue
        if not (0 <= e.ref < len(new)):
            continue
        t = new[e.ref]
        if getattr(t, "col_w", None) or t.n_cols < 2:
            continue
        tx0, ty0, tx1, ty1 = e.bbox
        pad_y = 0.02 * abs(ty1 - ty0) if ty1 != ty0 else 2.0
        spans: list[tuple[float, float]] = []
        for lx0, ly0, lx1, ly1 in by_page.get(e.page_no, []):
            cx, cy = (lx0 + lx1) / 2.0, (ly0 + ly1) / 2.0
            if tx0 - 2 <= cx <= tx1 + 2 and ty0 - pad_y <= cy <= ty1 + pad_y:
                spans.append((lx0, lx1))
        col_w = derive_col_widths(tx0, tx1, spans, t.n_cols)
        if col_w:
            new[e.ref] = _dc_replace(t, col_w=col_w)
    return new


class MineruNotInstalled(RuntimeError):
    pass


class MineruParser:
    name = "mineru"

    def __init__(
        self,
        backend: str = "pipeline",
        lang: str = "korean",
        method: str = "auto",  # auto | txt | ocr — CID 폰트 PDF는 txt 추출이 비어 ocr 필요
        timeout_s: int = 1800,
    ) -> None:
        self.backend = backend
        self.lang = lang
        self.method = method
        self.timeout_s = timeout_s

    @staticmethod
    def _cli() -> str:
        # venv로 실행하면 mineru가 PATH에 없을 수 있다 — 실행 중인 파이썬 옆을 먼저 본다
        beside_python = Path(sys.executable).parent / "mineru"
        if beside_python.exists():
            return str(beside_python)
        found = shutil.which("mineru")
        if found:
            return found
        raise MineruNotInstalled("mineru CLI를 찾을 수 없다. pip install -e '.[parsers]'")

    def parse(self, path: Path, work_dir: Path | None = None) -> ParseResult:
        cli = self._cli()
        out_dir = work_dir or path.parent / f"{path.stem}_mineru_out"
        t0 = time.perf_counter()
        proc = subprocess.run(
            [cli, "-p", str(path), "-o", str(out_dir), "-b", self.backend,
             "-l", self.lang, "-m", self.method],
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        elapsed = time.perf_counter() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"mineru 실패 (exit {proc.returncode}): {proc.stderr[-2000:]}")

        content_lists = sorted(out_dir.rglob("*_content_list.json"))
        if not content_lists:
            raise RuntimeError(f"content_list.json이 {out_dir} 아래에 없다")
        raw_entries = json.loads(content_lists[0].read_text(encoding="utf-8"))

        page_texts: dict[int, list[str]] = defaultdict(list)
        tables: list[ParsedTable] = []
        images: list[ParsedImage] = []
        entries: list[ParsedEntry] = []
        warnings: list[str] = []

        def _bbox(e: dict):
            bb = e.get("bbox")
            if isinstance(bb, (list, tuple)) and len(bb) == 4:
                try:
                    return tuple(float(v) for v in bb)
                except (TypeError, ValueError):
                    return None
            return None
        base_dir = content_lists[0].parent
        max_page = 0
        # 첫 쪽 머리 영역 — MinerU 는 첫 쪽 맨 위의 문서 이름·시행일도 'header'로 분류해
        # 버린다. 2쪽부터의 머리말은 쪽마다 반복되는 장식이지만, 첫 쪽의 그 자리는 문서
        # 이름이 놓이는 자리다(실측 2026-09-20 규정집: '… 사무분장 규정', '학과장회 통과일자 …'
        # 가 통째로 빠짐). 첫 쪽 것만 위에서부터 본문 앞에 되살린다.
        first_headers = sorted(
            (e for e in raw_entries
             if e.get("type") == "header" and int(e.get("page_idx", 0)) == 0
             and (e.get("text") or "").strip()),
            key=lambda e: (_bbox(e) or (0, 0, 0, 0))[1],
        )
        for i, e in enumerate(first_headers):
            txt = e["text"].strip()
            entries.append(ParsedEntry(page_no=1, kind="heading" if i == 0 else "text",
                                       text=txt, bbox=_bbox(e)))
            page_texts[1].append(f"[[h]]{txt}" if i == 0 else txt)
        for e in raw_entries:
            page_no = int(e.get("page_idx", 0)) + 1
            max_page = max(max_page, page_no)
            kind = e.get("type")
            if kind == "table":
                body = e.get("table_body") or ""
                # 캡션(첫 줄)·각주(다음 줄들)는 표 항목의 text로 — 표가 무엇을 담는지,
                # 단위가 무엇인지가 셀 수치의 문맥이다
                caption = _caption_lines(e.get("table_caption"), e.get("table_footnote"))
                if body:
                    tables.append(parse_html_table(body, page_no=page_no))
                    entries.append(ParsedEntry(
                        page_no=page_no, kind="table",
                        ref=len(tables) - 1, bbox=_bbox(e), text=caption,
                    ))
                    if caption:
                        page_texts[page_no].append(caption)
                else:
                    warnings.append(f"p{page_no}: table_body 없는 표 항목")
            elif kind == "image":
                img = e.get("img_path") or ""
                img_file = (base_dir / img).resolve() if img else None
                caption = _caption_lines(e.get("img_caption"), e.get("img_footnote"))
                if img_file and img_file.exists():
                    images.append(ParsedImage(page_no=page_no, path=img_file))
                    entries.append(ParsedEntry(
                        page_no=page_no, kind="image",
                        ref=len(images) - 1, bbox=_bbox(e), text=caption,
                    ))
                    # 본문 흐름 속 그림 위치 마커 — 구조 항목이 없는 소비자용 폴백
                    page_texts[page_no].append(f"[[img]]{img_file.name}")
                # 그림 캡션 텍스트도 본문에 남긴다
                if caption:
                    page_texts[page_no].append(caption)
            elif kind == "text":
                txt = e.get("text", "")
                if txt.strip():
                    entries.append(ParsedEntry(
                        page_no=page_no,
                        kind="heading" if e.get("text_level") else "text",
                        text=txt.strip(), bbox=_bbox(e),
                    ))
                # 제목 수준(text_level)은 마커로 남겨 구조화 저장에서 소제목이 된다
                if e.get("text_level"):
                    txt = f"[[h]]{txt}"
                page_texts[page_no].append(txt)

        pages = [
            # 블록(문단·제목·캡션) 경계를 빈 줄로 남긴다 — 구조화 저장이 블록 단위가 된다
            ParsedPage(page_no=i, text="\n\n".join(page_texts.get(i, [])))
            for i in range(1, max_page + 1)
        ]
        ocr_lines: list[dict] = []
        ocr_sizes: dict[int, tuple[float, float]] = {}
        middles = sorted(base_dir.glob("*_middle.json"))
        if middles:
            try:
                ocr_lines, ocr_sizes = extract_middle_lines(
                    json.loads(middles[0].read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                warnings.append("middle.json 줄 좌표 추출 실패")
        # 스캔·사진 표는 괘선이 없어 열 폭 정보가 없다 — OCR 글자줄 좌표에서
        # 열 경계를 인출해 채운다. 디지털 PDF 표는 뒤이어 괘선 직독으로 교체되며
        # 그쪽 col_w가 우선한다(호출부 swap_tables). 실패 시 균등 폭 폴백.
        if ocr_lines:
            tables = fill_ocr_col_widths(tables, entries, ocr_lines)
        return ParseResult(
            parser=self.name, elapsed_s=elapsed, pages=pages,
            tables=tables, images=images, entries=entries, warnings=warnings,
            ocr_lines=ocr_lines, ocr_page_sizes=ocr_sizes,
        )
