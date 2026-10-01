#!/usr/bin/env python3
"""사업별 뼈대 문서 고르기 — 원본 보관소에서 사업마다 기본계획·공고·계획서·실적보고서·평가·지침의 최종본만 고른다.

사업별 온톨로지의 일의 단위(평가지표 항목·단위과제·학과)는 경로가 아니라 뼈대 문서의 목차에서 나온다
(경로의 「01-2_」는 폴더 순번이지 과제 번호가 아니다 — docs/notes/2026-10-01-business-analysis.md). 그래서 뼈대 문서를
먼저 문서함에 들여 절 구조를 얻는다. 원본 전부가 아니라 고른 것만 VM 으로 간다(ADR-0047: 원본 보관은 DGX).

고르는 규칙(일반):
- 사업 분류는 graph/programs(자동 확정·폴더 추론 모두), 갈래는 뼈대 갈래만, 형식은 문서(hwpx·hwp·docx·pdf)만.
- 증빙 묶음(경로에 증빙·지출·스캔·입찰·구매·영수)은 뺀다 — 뼈대 문서가 아니라 그 근거 자료다.
- 같은 문서의 판본(이름에서 판·날짜·최종·제출 표기를 뗀 줄기가 같은 것)은 하나만: 제출·최종 > 수정 차수 > 판 번호 > 날짜,
  같으면 읽기 쉬운 형식(hwpx > hwp > docx > pdf).

사용(DGX): PYTHONPATH=src .venv-train/bin/python scripts/161_select_core.py --root ~/data --out ~/core_selection
  → 사업마다 <out>/<사업 id>.txt(원본 경로 한 줄씩)와 요약
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.graph import programs  # noqa: E402

EXT = {"hwp", "hwpx", "pdf", "docx", "doc", "xlsx", "xls", "pptx"}
DOC_RANK = {"hwpx": 0, "hwp": 1, "docx": 2, "doc": 3, "pdf": 4}
CORE = ("basic_plan", "announcement", "plan", "report", "evaluation", "criteria", "guideline")
EVIDENCE = re.compile(r"증빙|지출|스캔|입찰|구매|영수|SCAN", re.I)
_VERSION_BITS = re.compile(r"(?:최종\s*제출본?|최종본?|제출본?|수정\s*\d*\s*차?|ver[._\s]*\d+(?:\.\d+)?|v\d+(?:\.\d+)?|"
                           r"\(\d{1,2}\)|\(\d{8}[^)]*\)|(?<![\d.])\d{4,8}(?![\d.])|회색조|복사본|사본|copy|\s+\d+$)", re.I)


def stem(name: str) -> str:
    s = Path(name).stem
    s = _VERSION_BITS.sub(" ", s)
    return re.sub(r"[\s_\-().\[\]]+", "", s).lower()


def version_rank(name: str) -> tuple:
    s = Path(name).stem
    submitted = 1 if re.search(r"제출", s) else 0
    final = 1 if re.search(r"최종", s) else 0
    rev = max([int(x) for x in re.findall(r"수정\s*\(?(\d+)\s*차", s)] or [1 if "수정" in s else 0])
    ver = max([float(x) for x in re.findall(r"ver[._\s]*(\d+(?:\.\d+)?)|v(\d+\.\d+)", s, re.I) for x in x if x] or [0.0])
    date = max([x for x in re.findall(r"(?<!\d)(\d{6,8})(?!\d)", s)] or ["0"])
    return (submitted + final, rev, ver, date)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "data"))
    ap.add_argument("--out", default=str(Path.home() / "core_selection"))
    ap.add_argument("--min-files", type=int, default=10, help="이보다 작은 사업은 건너뛴다")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    docs, seen = [], set()
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower().lstrip(".") not in EXT:
            continue
        key = (f.name, f.stat().st_size)
        if key in seen:
            continue
        seen.add(key)
        rel = f.relative_to(root)
        docs.append({"id": len(docs), "filename": f.name, "path": str(rel.parent), "head": "", "abs": str(f),
                     "size": f.stat().st_size})
    cards = programs.build_cards(docs)
    res = programs.classify(docs, cards)
    programs.inherit_by_folder(docs, res)
    per_prog = defaultdict(list)
    for d, a in zip(docs, res):
        if a.program:
            per_prog[a.program].append((d, a))
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for pid, items in sorted(per_prog.items(), key=lambda t: -len(t[1])):
        if len(items) < args.min_files:
            continue
        groups = defaultdict(list)
        for d, a in items:
            ext = Path(d["filename"]).suffix.lower().lstrip(".")
            if a.kind not in CORE or ext not in DOC_RANK or EVIDENCE.search(f"{d['path']}/{d['filename']}"):
                continue
            when = a.round or a.year or (programs._PATH_YEAR.findall(d["path"]) or [""])[-1]
            groups[(a.kind, str(when), stem(d["filename"]))].append((d, a))
        chosen = []
        for (kind, when, _s), cands in groups.items():
            best = max(cands, key=lambda t: (version_rank(t[0]["filename"]),
                                              -DOC_RANK[Path(t[0]["filename"]).suffix.lower().lstrip(".")]))
            chosen.append({"kind": kind, "when": when, "path": best[0]["abs"], "mb": round(best[0]["size"] / 1e6, 1),
                           "versions": len(cands), "status": best[1].status})
        chosen.sort(key=lambda c: (c["kind"], c["when"], c["path"]))
        name = items[0][1].program_name
        safe = re.sub(r"[^0-9A-Za-z가-힣]+", "_", pid.split(":", 1)[1])
        (out_dir / f"{safe}.txt").write_text("\n".join(c["path"] for c in chosen) + "\n", encoding="utf-8")
        (out_dir / f"{safe}.json").write_text(json.dumps({"program": pid, "name": name, "chosen": chosen},
                                                          ensure_ascii=False, indent=1), encoding="utf-8")
        by_kind = defaultdict(int)
        for c in chosen:
            by_kind[c["kind"]] += 1
        summary[pid] = {"name": name, "files": len(items), "chosen": len(chosen),
                        "mb": round(sum(c["mb"] for c in chosen), 1), "by_kind": dict(by_kind), "list": f"{safe}.txt"}
        print(f"== {name} — 파일 {len(items)} → 뼈대 {len(chosen)}건 {summary[pid]['mb']}MB {dict(by_kind)}")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("기록:", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
