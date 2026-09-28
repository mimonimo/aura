#!/usr/bin/env python3
"""변환 회귀 검사를 문서함 밖 파일 묶음에도 — 폴더의 hwp·hwpx 전부를 145 와 같은 잣대(글자·표·그림·구조 오류)로 잰다.

  실행(VM): env PYTHONPATH=src .venv/bin/python scripts/149_convert_corpus_check.py --dir data/platform/backup/<날짜>/documents [--limit 50] [--kinds hwpx]
어떤 한글 문서든 깔끔하게(사용자 지시 2026-09-28) — 실문서가 많이 들어오기 전에 공개 문서 묶음으로 일반 결함을 찾는다. 등록하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import importlib  # noqa: E402

c145 = importlib.import_module("145_hwp_convert_check")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--kinds", default="hwp,hwpx")
    args = ap.parse_args()
    kinds = {"." + k.strip() for k in args.kinds.split(",")}
    files = sorted(p for p in Path(args.dir).rglob("원본.*") if p.suffix.lower() in kinds)
    if args.limit:
        files = files[: args.limit]
    from zzaimy.ingest import hwp5_docx, hwpx_docx

    warn = fail = 0
    print(f"{'형식':4} {'글자':>13} {'표':>9} {'그림':>7} {'초':>4}  판정  파일")
    for path in files:
        t0 = time.time()
        name = path.parent.name[:44]
        try:
            if path.suffix.lower() == ".hwpx":
                src = c145._source_counts_hwpx(path)
                data, _ = hwpx_docx.convert(path)
            else:
                xml = hwp5_docx.dump_xml(path)
                src = c145._source_counts_hwp5(xml)
                data, _ = hwp5_docx.convert_xml(xml)
            out = c145._docx_counts(data)
        except Exception as e:
            fail += 1
            print(f"{path.suffix[1:]:4} {'-':>13} {'-':>9} {'-':>7} {time.time() - t0:4.0f}  FAIL  {name} — {type(e).__name__}: {str(e)[:60]}")
            continue
        ratio = out["text"] / src["text"] if src["text"] else 1.0
        level, why = "PASS", []
        if out["fails"]:
            level = "FAIL"; why += out["fails"]
        elif ratio < 0.9 or ratio > 1.3:
            level = "WARN"; why.append(f"글자 {ratio:.2f}")
        elif out["tables"] < src["tables"]:
            level = "WARN"; why.append("표 부족")
        elif out["pics"] < src["pics"]:
            level = "WARN"; why.append("그림 부족")
        elif src.get("hf_pics") and not out.get("hf_pics"):
            level = "WARN"; why.append("머리말 그림 없음")
        elif src.get("page_num") and not out.get("page_fields"):
            level = "WARN"; why.append("쪽 번호 없음")
        warn += level == "WARN"; fail += level == "FAIL"
        print(f"{path.suffix[1:]:4} {out['text']:>6}/{src['text']:<6} {out['tables']:>4}/{src['tables']:<4} {out['pics']:>3}/{src['pics']:<3} {time.time() - t0:4.0f}  {level}  {name} {' '.join(why)}")
    print(f"파일 {len(files)}건 · WARN {warn} · FAIL {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
