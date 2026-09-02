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

from zzaimy.ingest.parsers.base import ParsedImage, ParsedPage, ParsedTable, ParseResult
from zzaimy.ingest.parsers.html_table import parse_html_table


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
        entries = json.loads(content_lists[0].read_text(encoding="utf-8"))

        page_texts: dict[int, list[str]] = defaultdict(list)
        tables: list[ParsedTable] = []
        images: list[ParsedImage] = []
        warnings: list[str] = []
        base_dir = content_lists[0].parent
        max_page = 0
        for e in entries:
            page_no = int(e.get("page_idx", 0)) + 1
            max_page = max(max_page, page_no)
            kind = e.get("type")
            if kind == "table":
                body = e.get("table_body") or ""
                if body:
                    tables.append(parse_html_table(body, page_no=page_no))
                else:
                    warnings.append(f"p{page_no}: table_body 없는 표 항목")
            elif kind == "image":
                img = e.get("img_path") or ""
                img_file = (base_dir / img).resolve() if img else None
                if img_file and img_file.exists():
                    images.append(ParsedImage(page_no=page_no, path=img_file))
                    # 본문 흐름 속 그림 위치 마커 — 구조화 저장에서 제자리에 배치된다
                    page_texts[page_no].append(f"[[img]]{img_file.name}")
                # 그림 캡션 텍스트도 본문에 남긴다
                for cap in e.get("img_caption") or []:
                    page_texts[page_no].append(cap)
            elif kind == "text":
                txt = e.get("text", "")
                # 제목 수준(text_level)은 마커로 남겨 구조화 저장에서 소제목이 된다
                if e.get("text_level"):
                    txt = f"[[h]]{txt}"
                page_texts[page_no].append(txt)

        pages = [
            # 블록(문단·제목·캡션) 경계를 빈 줄로 남긴다 — 구조화 저장이 블록 단위가 된다
            ParsedPage(page_no=i, text="\n\n".join(page_texts.get(i, [])))
            for i in range(1, max_page + 1)
        ]
        return ParseResult(
            parser=self.name, elapsed_s=elapsed, pages=pages,
            tables=tables, images=images, warnings=warnings,
        )
