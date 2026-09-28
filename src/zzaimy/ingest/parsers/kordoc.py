"""kordoc 어댑터 — 한글(hwp·hwpx) 등을 kordoc(Node, MIT) 으로 읽어 우리 ParseResult 로 옮긴다(ADR-0033).

왜: 옛 hwp 30MB 를 pyhwp 구조 XML 로 읽으면 3분, kordoc 은 1초(실측 2026-09-28). 표는 중첩·병합을 그대로 낸다.
어떻게: kordoc 은 어댑터 뒤에만 있다 — 판을 고정해 쓰고(설치 판은 실행 때 기록), 없거나 실패하면 호출부가 pyhwp 로 돌아간다.
마스킹·조각·색인·판독은 우리 것 그대로. 내장 OCR 은 쓰지 않는다(모델 4종 규칙).
설치(VM, root 없이): ~/opt/node(공식 tar), ~/opt/kordoc 에 `npm install kordoc` — HANDOFF 참조. 다른 자리는 ZZAIMY_KORDOC 로.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from zzaimy.ingest.parsers.base import ParsedEntry, ParsedImage, ParsedPage, ParsedTable, ParseResult, TableCell

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)
TIMEOUT_S = 300


def _bin() -> Path | None:
    """kordoc 실행 파일 — ZZAIMY_KORDOC → ~/opt/kordoc → PATH."""
    env = os.environ.get("ZZAIMY_KORDOC", "").strip()
    if env and Path(env).exists():
        return Path(env)
    home = Path.home() / "opt" / "kordoc" / "node_modules" / ".bin" / "kordoc"
    if home.exists():
        return home
    found = shutil.which("kordoc")
    return Path(found) if found else None


def _env() -> dict:
    env = dict(os.environ)
    node = Path.home() / "opt" / "node" / "bin"
    if node.exists():
        env["PATH"] = f"{node}{os.pathsep}{env.get('PATH', '')}"
    env.setdefault("KORDOC_OFFLINE", "1")          # 모델·자원 자동 내려받기 금지(OCR 안 씀)
    return env


def available() -> bool:
    return _bin() is not None and os.environ.get("ZZAIMY_KORDOC_OFF", "") != "1"


def version() -> str:
    b = _bin()
    if not b:
        return ""
    pkg = b.resolve().parent.parent / "kordoc" / "package.json"
    try:
        return json.loads(pkg.read_text(encoding="utf-8")).get("version", "")
    except Exception:
        return ""


def _cell_text(raw: str) -> str:
    """셀 글 — 중첩 표 HTML 은 칸을 공백으로, 줄바꿈은 유지. 우리 표 조각은 셀 하나에 글만 둔다."""
    s = _BR.sub("\n", str(raw or ""))
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return "\n".join(" ".join(ln.split()) for ln in s.replace("\r", "\n").splitlines() if ln.strip())


def _table(tb: dict, page_no: int) -> ParsedTable | None:
    """kordoc 표(cells: 행마다 셀 목록, colSpan/rowSpan) → 좌표를 가진 셀 — 병합으로 밀린 자리는 점유 격자로 찾는다."""
    rows = tb.get("cells") or []
    n_rows = int(tb.get("rows") or len(rows))
    n_cols = int(tb.get("cols") or max((len(r) for r in rows), default=0))
    if not rows or n_rows <= 0 or n_cols <= 0:
        return None
    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []
    for r, row in enumerate(rows[:n_rows]):
        c = 0
        for cell in row:
            while (r, c) in occupied:
                c += 1
            if c >= n_cols:
                break
            rs = max(int(cell.get("rowSpan") or 1), 1)
            cs = max(int(cell.get("colSpan") or 1), 1)
            cells.append(TableCell(row=r, col=c, text=_cell_text(cell.get("text")), row_span=rs, col_span=cs,
                                   is_header=bool(cell.get("isHeader") or cell.get("header"))))
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
            c += cs
    return ParsedTable(page_no=page_no, n_rows=n_rows, n_cols=n_cols, cells=tuple(cells))


def from_json(data: dict, image_dir: Path | None = None, parser_name: str = "kordoc") -> ParseResult:
    """kordoc `--format json` 결과 → ParseResult. 그림은 image_dir/<이름> 파일(--image-refs)이 있을 때만 싣는다."""
    t0 = time.perf_counter()
    tables: list[ParsedTable] = []
    images: list[ParsedImage] = []
    entries: list[ParsedEntry] = []
    page_lines: dict[int, list[str]] = {}
    warnings: list[str] = []
    for b in data.get("blocks") or []:
        try:
            page_no = max(int(b.get("pageNumber") or 1), 1)
        except (TypeError, ValueError):
            page_no = 1
        kind = b.get("type")
        lines = page_lines.setdefault(page_no, [])
        if kind in ("paragraph", "heading", "list", "quote"):
            text = " ".join(str(b.get("text") or "").split())
            if not text:
                continue
            entries.append(ParsedEntry(page_no=page_no, kind="heading" if kind == "heading" else "text", text=text))
            lines.append(text)
        elif kind == "table":
            t = _table(b.get("table") or {}, page_no)
            if t is None:
                continue
            tables.append(t)
            entries.append(ParsedEntry(page_no=page_no, kind="table", ref=len(tables) - 1))
        elif kind == "image":
            name = str(b.get("text") or b.get("path") or "").strip()
            path = (image_dir / name) if (image_dir and name) else None
            if path is None or not path.exists():
                warnings.append(f"그림 파일 없음: {name}")
                continue
            images.append(ParsedImage(page_no=page_no, path=path))
            entries.append(ParsedEntry(page_no=page_no, kind="image", ref=len(images) - 1))
            lines.append(f"[[img]]{path.name}")
    pages = [ParsedPage(page_no=p, text="\n\n".join(page_lines[p])) for p in sorted(page_lines)]
    return ParseResult(parser=parser_name, elapsed_s=time.perf_counter() - t0, pages=pages, tables=tables,
                       images=images, entries=entries, warnings=warnings)


class KordocParser:
    name = "kordoc"

    def __init__(self, timeout_s: int = TIMEOUT_S) -> None:
        self.timeout_s = timeout_s

    def parse(self, path: Path, work_dir: Path | None = None) -> ParseResult:
        exe = _bin()
        if exe is None:
            raise RuntimeError("kordoc 이 없다 — ~/opt/kordoc 에 설치하거나 ZZAIMY_KORDOC 을 준다")
        work = Path(work_dir) if work_dir else path.parent / f"{path.stem}_kordoc"
        work.mkdir(parents=True, exist_ok=True)
        out = work / "kordoc.json"
        t0 = time.perf_counter()
        proc = subprocess.run([str(exe), str(path), "--format", "json", "--image-refs", "-o", str(out)],
                              capture_output=True, text=True, timeout=self.timeout_s, env=_env())
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"kordoc 실패({proc.returncode}): {(proc.stderr or proc.stdout)[-300:]}")
        data = json.loads(out.read_text(encoding="utf-8"))
        if not data.get("success", True):
            raise RuntimeError(f"kordoc 결과 실패: {str(data.get('error') or '')[:200]}")
        result = from_json(data, image_dir=work / "images")
        result.warnings.insert(0, f"kordoc {version()} · {time.perf_counter() - t0:.1f}s")
        return result
