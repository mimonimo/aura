#!/usr/bin/env python3
"""표 갈래별 디자인 값 캐기 — 반입 원본(학교 서식·계획서·보고서 hwpx·hwp)의 표를 갈래로 나눠 갈래마다 최빈 모양을 data/platform/table_styles.json 에.

에이전트가 새 표를 만들 때 갈래(export/table_kinds)를 가려 이 값을 입힌다(app.table_styles). 원본은 읽기만 한다.
파생 결과물이라 입력(고른 문서 목록·크기·시각)의 지문이 같으면 다시 만들지 않는다.

  env PYTHONPATH=src .venv/bin/python scripts/230_table_styles.py                 # data/platform/documents/반입 전부
  ... --sample 200        (큰 것부터가 아니라 고르게 n 건 — 무거우면)
  ... --ext hwpx          (hwpx 만 — .hwp 는 건마다 구조 풀기가 길다)
  ... --hwp-sample 120    (hwpx 전부 + .hwp 고르게 120 건)
  ... --print             (쓰지 않고 결과 JSON 을 표준 출력으로)

고르기: 반입 폴더의 원본.hwpx·원본.hwp. AURA 가 만든 작업본(기안기·생성)은 넣지 않는다 — 우리 모양을 다시 배우게 된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import table_styles as ts  # noqa: E402
from zzaimy.export import table_kinds as tk  # noqa: E402


def _even(files: list[Path], n: int | None) -> list[Path]:
    if n and len(files) > n:
        step = len(files) / n                             # 고르게 — 폴더 차례(연도·갈래)에 치우치지 않게
        files = [files[int(i * step)] for i in range(n)]
    return files


def pick(root: Path, sample: int | None, exts: tuple[str, ...] = (".hwpx", ".hwp"), hwp_sample: int | None = None) -> list[Path]:
    files = sorted(p for p in root.rglob("원본.*") if p.suffix.lower() in exts and p.is_file())
    if hwp_sample:
        files = [p for p in files if p.suffix.lower() != ".hwp"] + _even([p for p in files if p.suffix.lower() == ".hwp"], hwp_sample)
    return _even(files, sample)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    plat = Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db").parent
    ap.add_argument("--root", type=Path, default=plat / "documents" / "반입")
    ap.add_argument("--files", type=Path, nargs="*")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--ext", default="hwpx,hwp", help="고를 확장자(쉼표) — .hwp 는 구조 풀기가 느리다")
    ap.add_argument("--hwp-sample", type=int, default=0, help="hwpx 는 전부, .hwp 는 고르게 n 건")
    ap.add_argument("--out", type=Path, default=plat)
    ap.add_argument("--print", dest="show", action="store_true")
    a = ap.parse_args(argv)
    files = list(a.files or []) or pick(a.root, a.sample or None, tuple("." + e.strip(".").lower() for e in a.ext.split(",") if e.strip()), a.hwp_sample or None)
    if not files:
        print("고른 문서가 없습니다", file=sys.stderr)
        return 2
    stamp = hashlib.sha256(json.dumps([[str(f), f.stat().st_size, int(f.stat().st_mtime)] for f in files]).encode()).hexdigest()[:16]
    log = (lambda m: print(m, file=sys.stderr))
    if not a.show and ts.load(a.out).get("stamp") == stamp:
        print("입력이 같아 그대로 둡니다:", ts.path(a.out))
        return 0
    log(f"문서 {len(files)}건")
    prof = ts.mine(files, log=log)
    prof["stamp"] = stamp
    if a.show:
        print(json.dumps(prof, ensure_ascii=False, indent=1))
    else:
        ts.save(a.out, prof)
        print("저장:", ts.path(a.out))
    for k in tk.KINDS:
        got = prof["kinds"].get(k)
        if got:
            v = got["values"]
            hf = (v.get("head_fill") or {}).get("mode", "-")
            log(f"  {tk.KIND_NAMES[k]:<10} 표 {got['n']:>5} · 문서 {got['docs']:>4} · 머리 음영 {hf} · 채택 값 {len(ts.mined_design(k, prof))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
