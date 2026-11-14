#!/usr/bin/env python3
"""기관 서식 프로필 만들기 — 그 기관이 직접 쓴 계획서·보고서에서 역할별 글자 모양(글꼴·크기·굵기)을 캐 data/platform/house_profile.json 에.

양식이 본문 모양을 정하지 않은 자리(작성방법 안내만 있는 장·절)와 에이전트가 새로 만드는 표의 칸 글자가 이 프로필을 따른다(app.house_profile).
파생 결과물이라 입력(고른 문서 목록·크기·시각)의 지문이 같으면 다시 만들지 않는다 — 원본이 바뀌면 다음 실행에서 따라온다.

  env PYTHONPATH=src .venv/bin/python scripts/229_house_profile.py --inst 영남이공             # data/inbox 에서 고름
  env PYTHONPATH=src .venv/bin/python scripts/229_house_profile.py --files a.hwpx b.hwp         # 고른 문서로
  ... --dry  (고른 문서만 보이고 쓰지 않음)

고르기: 이름이나 경로에 기관 이름(--inst)이 들고, 계획서·보고서이며, 양식·서식·작성요령·공고·안내·평가 결과가 아닌 한글 문서.
같은 문서의 사본(이름이 같은 파일)은 큰 것 하나만. 큰 것부터 --max 개(기본 6).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import house_profile as hp  # noqa: E402

SKIP = re.compile(r"양식|서식|작성요령|공고|안내|평가\s*결과|종합의견|요약본")
KIND = re.compile(r"계획서|보고서")


def pick(root: Path, inst: str, limit: int) -> list[Path]:
    seen: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.suffix.lower() not in (".hwpx", ".hwp") or not p.is_file():
            continue
        s = str(p)
        if inst not in s or not KIND.search(p.name) or SKIP.search(p.name):
            continue
        key = re.sub(r"[_\s\d]+", "", p.stem)                 # 날짜·판 번호만 다른 사본은 한 문서로
        if key not in seen or p.stat().st_size > seen[key].stat().st_size:
            seen[key] = p
    return sorted(seen.values(), key=lambda x: -x.stat().st_size)[:limit]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inst", help="기관 이름(경로·이름에 든 글자)")
    ap.add_argument("--root", type=Path, default=ROOT / "data" / "inbox")
    ap.add_argument("--files", type=Path, nargs="*")
    ap.add_argument("--max", type=int, default=6)
    ap.add_argument("--out", type=Path, default=Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db").parent)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    files = list(a.files or []) or (pick(a.root, a.inst, a.max) if a.inst else [])
    if not files:
        print("고른 문서가 없습니다 — --inst <기관 이름> 또는 --files", file=sys.stderr)
        return 2
    stamp = hashlib.sha256(json.dumps([[str(f), f.stat().st_size, int(f.stat().st_mtime)] for f in files]).encode()).hexdigest()[:16]
    for f in files:
        print(f"  {f.stat().st_size / 1e6:7.1f}MB  {f.name[:70]}")
    if a.dry:
        return 0
    old = hp.load(a.out)
    if old.get("stamp") == stamp:
        print("입력이 같아 그대로 둡니다:", hp.path(a.out))
        return 0
    prof = hp.mine(files)
    prof["stamp"] = stamp
    prof["inst"] = a.inst or ""
    hp.save(a.out, prof)
    print("저장:", hp.path(a.out))
    for r, v in prof["roles"].items():
        print(f"  {r:<12} {v['font']} {v['pt']}pt{' 굵게' if v['bold'] else ''}  (글자 {v['chars']:,}, 같은 글꼴 {v['share']:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
