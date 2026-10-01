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
    # .bin/kordoc 은 node_modules/kordoc/dist/cli.js 로의 링크 — 위로 올라가며 이름이 kordoc 인 package.json 을 찾는다
    for base in (b.resolve(), b):
        for parent in [base] + list(base.parents):
            pkg = parent / "package.json"
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("name") == "kordoc":
                return str(data.get("version", ""))
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
    # kordoc 의 cells 는 행마다 열 수만큼 다 채운 격자다 — 병합 셀 뒤에는 빈 자리표 셀이 온다(실측 2026-09-28: 앞판은 자리표를
    # 새 셀로 세어 열이 밀리고 7천 자를 잃었다). 격자 좌표를 그대로 쓰고, 앞 셀의 span 이 덮는 자리는 건너뛴다.
    n_rows = max(n_rows, len(rows))
    n_cols = max(n_cols, max((len(r) for r in rows), default=0))
    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if (r, c) in occupied:
                continue
            rs = max(int(cell.get("rowSpan") or 1), 1)
            cs = max(int(cell.get("colSpan") or 1), 1)
            cells.append(TableCell(row=r, col=c, text=_cell_text(cell.get("text")), row_span=rs, col_span=cs,
                                   is_header=bool(cell.get("isHeader") or cell.get("header"))))
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
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
        no_images = False
        if proc.returncode != 0 or not out.exists():
            # 그림이 아주 많이 박힌 큰 한글 파일은 구조는 읽고 그림 내보내기에서 실패한다(실측 2026-10-01: LINC3.0 사업계획서
            # 108MB — '[1/1] OK' 뒤 '문서 처리 중 오류', --no-images 는 3초에 성공). 구조가 더 중요하니 그림 없이 한 번 더 읽는다
            first = (proc.stderr or proc.stdout)[-200:]
            out.unlink(missing_ok=True)
            proc = subprocess.run([str(exe), str(path), "--format", "json", "--no-images", "-o", str(out)],
                                  capture_output=True, text=True, timeout=self.timeout_s, env=_env())
            if proc.returncode != 0 or not out.exists():
                raise RuntimeError(f"kordoc 실패({proc.returncode}): {first} / 그림 없이도: {(proc.stderr or proc.stdout)[-200:]}")
            no_images = True
        data = json.loads(out.read_text(encoding="utf-8"))
        if not data.get("success", True):
            raise RuntimeError(f"kordoc 결과 실패: {str(data.get('error') or '')[:200]}")
        result = from_json(data, image_dir=work / "images")
        result.warnings.insert(0, f"kordoc {version()} · {time.perf_counter() - t0:.1f}s" + (" · 그림 없이(그림 내보내기 실패)" if no_images else ""))
        return result
