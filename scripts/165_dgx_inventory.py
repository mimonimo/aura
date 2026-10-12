#!/usr/bin/env python3
"""DGX 원본 보관소 전체 목록 — 파일마다 경로·크기·수정 시각·형식·사업·갈래·연차(JSONL). VM 의 원본 목록 장부(app/archive)로 들인다.

분류는 파일 이름·경로로 한다(scripts/160 과 같은 규칙). 같은 파일(dup_of)은 이름·크기·내용 지문(sha256)이 모두 같은 앞의 파일을
가리킨다 — 이름·크기가 같아도 내용이 다르면 중복이 아니다(zzaimy/archive_dups). 지문은 이름·크기가 같은 문서 묶음만 읽어 내고,
확장자 없음·「…다운로드」 파일은 파일 머리로 실제 문서 형식을 본다. 둘 다 파일 사실 장부(--facts)에 쌓아 같은 판은 다시 읽지 않고,
VM(scripts/170)이 그 장부를 받아 같은 판정을 한다.

사용(DGX): PYTHONPATH=src .venv-parse/bin/python scripts/165_dgx_inventory.py --root ~/data --facts ~/parsed/archive_facts.jsonl \
           > ~/archive_inventory.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy import archive_dups  # noqa: E402
from zzaimy.graph import programs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "data"))
    ap.add_argument("--facts", default="", help="파일 사실 장부(JSONL: 지문·실제 형식) — 읽어 같은 판은 다시 읽지 않고, 끝에 새로 쓴다")
    ap.add_argument("--hash-budget", type=float, default=1200, help="이번 회차 지문 읽기 시간 상한(초) — 남은 것은 다음 회차에")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    files = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.name.startswith(".") or f.name.startswith("~$"):
            continue
        st = f.stat()
        rel = str(f.relative_to(root))
        files.append({"id": len(files), "filename": f.name, "path": str(Path(rel).parent), "head": "", "rel": rel,
                      "size": st.st_size, "mtime": int(st.st_mtime), "ext": f.suffix.lower().lstrip("."),
                      "area": rel.split("/", 1)[0]})
    facts = archive_dups.load_facts(args.facts) if args.facts else {}
    n_real = archive_dups.apply_real_ext(files, facts, root=root, use_mtime=True)
    got = archive_dups.fill_digests(files, facts, root, budget_s=args.hash_budget)
    dups = archive_dups.assign_dups(files, facts, use_mtime=True)
    if args.facts:
        archive_dups.save_facts(args.facts, facts, keep={f["rel"] for f in files})
    print(f"지문 새로 {got['hashed']} · 남음 {got['pending']} · 읽기 실패 {got['failed']} · 내용이 달라 중복 아님 {dups['split_by_content']}"
          f" · 지문 몰라 이름·크기로 {dups['unknown_digest']} · 실제 형식으로 본 확장자 없는 문서 {n_real}", file=sys.stderr)
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
