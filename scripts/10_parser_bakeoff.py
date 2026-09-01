"""파서 bake-off — MinerU ↔ Docling 나란히 비교 (W1-W2 TASK-04).

같은 입력에 두 파서를 돌려 재현 가능한 지표로 비교 리포트를 만든다.
리포트는 data/interim/(gitignore 대상)에 쓰며, **원문 텍스트를 리포트에
싣지 않는다** — 지표·개수·시간만 기록한다.

사용:
    python scripts/10_parser_bakeoff.py data/demo/synthetic_sample.pdf \
        --truth data/demo/synthetic_sample.truth.json
    python scripts/10_parser_bakeoff.py <실물.pdf>   # 정답 없이 기술 통계만
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from zzaimy.ingest.parsers.base import ParseResult
from zzaimy.ingest.parsers.metrics import BakeoffScore, load_ground_truth, score


def make_parsers(names: list[str], mineru_method: str):
    out = []
    for n in names:
        if n == "docling":
            from zzaimy.ingest.parsers.docling import DoclingParser

            out.append(DoclingParser())
        elif n == "mineru":
            from zzaimy.ingest.parsers.mineru import MineruParser

            out.append(MineruParser(method=mineru_method))
        else:
            raise SystemExit(f"모르는 파서: {n}")
    return out


def describe(r: ParseResult) -> dict:
    return {
        "parser": r.parser,
        "elapsed_s": round(r.elapsed_s, 2),
        "n_pages": len(r.pages),
        "n_tables": len(r.tables),
        "n_merged_cells": sum(len(t.merged_cells) for t in r.tables),
        "table_shapes": [f"p{t.page_no}:{t.n_rows}x{t.n_cols}" for t in r.tables],
        "total_text_chars": sum(len(p.text) for p in r.pages),
        "warnings": r.warnings,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--truth", type=Path, default=None)
    ap.add_argument("--parsers", default="docling,mineru")
    ap.add_argument("--mineru-method", default="auto", choices=["auto", "txt", "ocr"])
    ap.add_argument("--out", type=Path, default=Path("data/interim/bakeoff"))
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    truth = load_ground_truth(args.truth) if args.truth else None

    rows: list[dict] = []
    scores: list[BakeoffScore] = []
    for parser in make_parsers(args.parsers.split(","), args.mineru_method):
        try:
            result = parser.parse(args.input)
        except Exception as e:  # 실패도 결과다 — 기록하고 계속
            rows.append({"parser": parser.name, "error": f"{type(e).__name__}: {e}"})
            traceback.print_exc()
            continue
        row = describe(result)
        if truth:
            s = score(result, truth)
            scores.append(s)
            row |= {k: v for k, v in dataclasses.asdict(s).items() if k not in ("parser",)}
        rows.append(row)

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    report = args.out / f"bakeoff-{args.input.stem}-{stamp}.md"
    lines = [
        f"# 파서 bake-off — {args.input.name}",
        "",
        f"> 생성: {stamp} · 정답 파일: {args.truth.name if args.truth else '없음(기술 통계만)'}",
        "",
    ]
    for row in rows:
        lines.append(f"## {row.get('parser', '?')}")
        lines.append("")
        lines.append("| 지표 | 값 |")
        lines.append("|---|---|")
        for k, v in row.items():
            if k != "parser":
                lines.append(f"| {k} | {v} |")
        lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    (report.with_suffix(".json")).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"리포트: {report}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
