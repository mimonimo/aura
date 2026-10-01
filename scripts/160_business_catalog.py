#!/usr/bin/env python3
"""원본 보관소 전체로 사업 카드를 만들고 파일마다 사업을 매겨 본다 — 사업 분류가 파일럿 밖에서도 서는지 사업별로 잰다.

graph/programs 의 일반 규칙(build_cards·classify)을 그대로 쓴다. 본문은 열지 않고 파일 이름·경로만 쓴다
(앞머리를 쓰는 반입 뒤 분류보다 단서가 적다 — 여기서 서는 사업은 반입 뒤에도 선다).

사업마다: 파일 수, 자동 확정 비율, 검토 대기 이유, 갈래·연차 분포, 다른 이름. 어느 사업에도 안 붙은 파일은 폴더별로 센다.

사용(DGX): PYTHONPATH=src .venv-train/bin/python scripts/160_business_catalog.py --root ~/data --out ~/business_catalog.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.graph import programs  # noqa: E402

EXT = {"hwp", "hwpx", "pdf", "docx", "doc", "xlsx", "xls", "pptx"}
DOC = {"hwp", "hwpx", "pdf", "docx", "doc"}
CORE = ("basic_plan", "announcement", "plan", "report", "evaluation", "criteria", "guideline")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "data"))
    ap.add_argument("--out", default="business_catalog.json")
    ap.add_argument("--min-files", type=int, default=5, help="이보다 적게 붙은 카드는 목록에서 접는다")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    docs, seen = [], set()
    for i, f in enumerate(sorted(root.rglob("*"))):
        if not f.is_file() or f.suffix.lower().lstrip(".") not in EXT:
            continue
        key = (f.name, f.stat().st_size)
        if key in seen:
            continue
        seen.add(key)
        rel = f.relative_to(root)
        docs.append({"id": len(docs), "filename": f.name, "path": str(rel.parent), "head": "", "area": rel.parts[0]})
    cards = programs.build_cards(docs)
    res = programs.classify(docs, cards)
    n_folder = programs.inherit_by_folder(docs, res)
    by = defaultdict(list)
    for d, a in zip(docs, res):
        by[a.program or "(없음)"].append((d, a))
    names = {c.node_id: c for c in cards}
    out = {"files": len(docs), "cards": len(cards), "programs": {}}
    print(f"파일 {len(docs)}(이름·크기 중복 제외) · 사업 카드 {len(cards)} · 폴더 추론 {n_folder}")
    for pid, items in sorted(by.items(), key=lambda t: -len(t[1])):
        n = len(items)
        auto = sum(1 for _, a in items if a.status == "auto")
        folder = sum(1 for _, a in items if a.status == "folder")
        c = names.get(pid)
        row = {"name": c.name if c else "", "n": n, "auto": auto, "folder": folder,
               "aliases": sorted(set(c.names) | set(c.acrs))[:8] if c else [],
               "areas": dict(Counter(d["area"] for d, _ in items).most_common()),
               "kind": dict(Counter(a.kind or "미정" for _, a in items).most_common(8)),
               "round": dict(sorted(Counter(a.round for _, a in items if a.round).items())),
               "year": dict(sorted(Counter(a.year for _, a in items if a.year).items())),
               "review_eg": [f"{d['path'][-60:]}/{d['filename'][:50]} — {'; '.join(a.evidence[:2])}"
                             for d, a in items if a.status == "review"][:4]}
        # 사업의 뼈대 문서 — 기본계획·공고·계획서·실적보고서·평가·지침(문서 형식만), 연차·연도별
        core = defaultdict(list)
        grid = defaultdict(Counter)
        for d, a in items:
            if a.kind in CORE and Path(d["filename"]).suffix.lower().lstrip(".") in DOC:
                when = f"{a.round}차년도" if a.round else (str(a.year) if a.year else (programs._PATH_YEAR.findall(d["path"]) or ["?"])[-1])
                core[a.kind].append(f"{when} · {d['path'][-50:]}/{d['filename'][:60]}")
                grid[a.kind][when] += 1
        row["core"] = {k: sorted(v) for k, v in core.items()}
        row["grid"] = {k: dict(sorted(v.items())) for k, v in grid.items()}
        out["programs"][pid] = row
        if n < args.min_files:
            continue
        print(f"\n== {row['name'] or pid} — {n}건 · 자동 {auto}({auto / n:.0%}) · 폴더 추론 {folder} · 영역 {row['areas']}")
        print("   다른 이름:", " · ".join(row["aliases"]))
        print("   갈래:", row["kind"], "| 연차:", row["round"], "| 연도:", row["year"])
        for e in row["review_eg"][:2]:
            print("   검토:", e)
    none = [d for d, _ in by.get("(없음)", [])]
    folders = Counter("/".join(Path(d["path"]).parts[:2]) for d in none)
    out["unassigned_folders"] = dict(folders.most_common(40))
    print(f"\n사업 못 붙은 파일 {len(none)} — 많은 폴더:")
    for k, v in folders.most_common(15):
        print(f"   {v}  {k}")
    Path(args.out).expanduser().write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n기록:", Path(args.out).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
