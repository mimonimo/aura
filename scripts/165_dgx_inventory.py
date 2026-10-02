#!/usr/bin/env python3
"""DGX 원본 보관소 전체 목록 — 파일마다 경로·크기·수정 시각·형식·사업·갈래·연차(JSONL). VM 의 원본 목록 장부(app/archive)로 들인다.

본문은 열지 않는다(파일 이름·경로로 분류, scripts/160 과 같은 규칙). 이름·크기가 같은 파일은 앞의 것을 dup_of 로 가리킨다.

사용(DGX): PYTHONPATH=src .venv-train/bin/python scripts/165_dgx_inventory.py --root ~/data > ~/archive_inventory.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.graph import programs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "data"))
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    files, first = [], {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.name.startswith(".") or f.name.startswith("~$"):
            continue
        st = f.stat()
        rel = str(f.relative_to(root))
        key = (f.name, st.st_size)
        dup = first.get(key, "")
        first.setdefault(key, rel)
        files.append({"id": len(files), "filename": f.name, "path": str(Path(rel).parent), "head": "", "rel": rel,
                      "size": st.st_size, "mtime": int(st.st_mtime), "ext": f.suffix.lower().lstrip("."),
                      "area": rel.split("/", 1)[0], "dup_of": dup})
    docs = [d for d in files if not d["dup_of"]]
    cards = programs.build_cards(docs)
    res = {a.doc_id: a for a in programs.classify(docs, cards)}
    programs.inherit_by_folder(docs, [res[d["id"]] for d in docs])
    by_rel = {d["rel"]: res[d["id"]] for d in docs}
    for d in files:
        a = by_rel.get(d["dup_of"] or d["rel"])
        out = {k: d[k] for k in ("rel", "size", "mtime", "ext", "area", "dup_of")}
        if a is not None:
            out.update({"program": a.program, "program_name": a.program_name, "status": a.status, "kind": a.kind,
                        "year": a.year, "round": a.round})
        print(json.dumps(out, ensure_ascii=False))
    print(f"파일 {len(files)} · 중복 제외 {len(docs)} · 사업 카드 {len(cards)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
