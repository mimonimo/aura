#!/usr/bin/env python3
"""사업별 문서 구성 조사 — 원본 보관소(DGX ~/data)를 파일 이름·경로만으로 훑어 사업마다 무엇이 있는지 센다.

본문은 열지 않는다(원본은 DGX 밖으로 나가지 않는다). 사업마다:
  갈래(계획·실적보고·평가기준·공고·지침·서식·표…) × 연차(N차년도·연도) × 파일 형식,
  같은 이름·크기 중복, 핵심 문서 후보(계획·보고·평가·공고·지침 중 hwp/hwpx/pdf/docx)
를 내고, 그래프·온톨로지 작업 순서를 사업마다 정하는 근거로 쓴다(ADR-0048, 사업 분류 먼저).

사업 단위: 보관소 맨 위 폴더(영역) 아래 첫 폴더. 그 폴더 이름이 연차·번호뿐이면(「01_1차년도(2025년)」)
영역 자체를 사업으로 본다.

사용(DGX): .venv-train/bin/python scripts/159_business_profile.py --root ~/data --out ~/business_profile.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.doc_routing import guess_kind  # noqa: E402

_ROUND = re.compile(r"([1-9])\s*차\s*년도")
_YEAR = re.compile(r"(?<!\d)(20[12]\d)(?!\d)")
_ONLY_TIME = re.compile(r"^[\d\s_.\-]*(?:[1-9]\s*차\s*년도)?[\s_\-]*(?:\(?\s*20[12]\d\s*년?\s*\)?)?[\s_\-]*$")
DOC_EXT = {"hwp", "hwpx", "pdf", "docx", "doc"}
CORE_KINDS = {"plan", "basic_plan", "report", "criteria", "announcement", "guideline", "regulation"}


def business_of(rel: Path) -> tuple[str, str]:
    parts = rel.parts
    area = parts[0]
    if len(parts) < 3:
        return area, area
    sub = parts[1]
    return area, (area if _ONLY_TIME.match(sub) else f"{area}/{sub}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "data"))
    ap.add_argument("--out", default="business_profile.json")
    ap.add_argument("--show", type=int, default=6, help="사업마다 보일 핵심 문서 예")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    biz: dict[str, dict] = defaultdict(lambda: {"files": 0, "bytes": 0, "kind": Counter(), "ext": Counter(),
                                                "round": Counter(), "year": Counter(), "core": [], "dup": 0})
    seen: set[tuple[str, int]] = set()
    for f in root.rglob("*"):
        if not f.is_file() or f.name.startswith("."):
            continue
        rel = f.relative_to(root)
        area, key = business_of(rel)
        b = biz[key]
        size = f.stat().st_size
        b["files"] += 1
        b["bytes"] += size
        ext = f.suffix.lower().lstrip(".")
        b["ext"][ext] += 1
        if (f.name, size) in seen:
            b["dup"] += 1
            continue
        seen.add((f.name, size))
        if ext not in DOC_EXT:
            continue
        kind, _why = guess_kind(f.name, "")
        b["kind"][kind or "미정"] += 1
        path_s = str(rel)
        r = _ROUND.findall(path_s)
        if r:
            b["round"][f"{r[-1]}차년도"] += 1
        y = _YEAR.findall(path_s)
        if y:
            b["year"][y[-1]] += 1
        if kind in CORE_KINDS:
            b["core"].append({"kind": kind, "path": path_s, "round": r[-1] if r else "", "year": y[-1] if y else ""})
    out = {}
    for key in sorted(biz, key=lambda k: -biz[k]["files"]):
        b = biz[key]
        core_by_kind = Counter(c["kind"] for c in b["core"])
        out[key] = {"files": b["files"], "gb": round(b["bytes"] / 1e9, 2), "dup": b["dup"],
                    "ext": dict(b["ext"].most_common(6)), "kind": dict(b["kind"].most_common()),
                    "round": dict(sorted(b["round"].items())), "year": dict(sorted(b["year"].items())),
                    "core_n": dict(core_by_kind), "core": b["core"]}
        print(f"\n== {key} — 파일 {b['files']} · {out[key]['gb']}GB · 중복 {b['dup']}")
        print("   갈래:", ", ".join(f"{k} {v}" for k, v in b["kind"].most_common(8)))
        if b["round"] or b["year"]:
            print("   연차:", dict(sorted(b["round"].items())), "연도:", dict(sorted(b["year"].items())))
        for c in sorted(b["core"], key=lambda c: (c["kind"], c["path"]))[: args.show]:
            print(f"   · [{c['kind']}] {c['path'][:110]}")
    dest = Path(args.out).expanduser()
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n기록:", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
