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


# 파일 이름의 일 단위 코드(단위과제 2-3, 평가지표 2-11) — 폴더 순번(「01-2_」)과 섞이지 않게 파일 이름에서만 본다
_UNIT = re.compile(r"(?<![\d.\-])\[?(\d{1,2}-\d{1,2})\]?(?![\d.\-])")
# 문서의 머리 낱말 — 판본마다 앞뒤 꾸밈(서식 번호·기관명·작업자·편집완료)이 달라도 이것과 일 단위 코드는 같다
_HEAD = re.compile(r"(수정\s*)?(과제계획서|사업계획서|수행계획서|운영계획서|실적보고서|연차보고서|결과보고서|성과보고서|"
                   r"평가\s*결과|종합\s*의견서?|기본계획|시행계획|공고문?|지침|편람|매뉴얼|계획서|보고서)")


# 사업 단위의 계획·보고만 뼈대다 — 프로그램 단위(캡스톤 실시계획서·공용장비 결과보고서·기자재 구입계획서·과제 수행계획서)는
# 그 사업의 실적 근거이지 뼈대가 아니다. 양식·샘플·신청서는 빈 틀이다.
_BUSINESS_HEAD = re.compile(r"과제계획서|사업\s*계획서|사업\s*수행\s*계획서|실적\s*보고서|연차\s*보고서|성과\s*보고서|기본\s*계획|"
                            r"시행\s*계획|자체\s*평가|선정\s*평가|종합\s*의견|평가\s*결과")
_BLANK_FORM = re.compile(r"양식|서식\s*\d|샘플|신청서|작성\s*요령")


def is_core(d: dict, kind: str, surfaces: set[str]) -> bool:
    if kind not in ("plan", "report"):
        return True
    name = Path(d["filename"]).stem
    if _BLANK_FORM.search(name) and not re.search(r"과제계획서|사업\s*계획서", name):
        return False                                      # 「[서식6] 2차년도 과제계획서_2-3」은 채운 계획서다
    return bool(_BUSINESS_HEAD.search(name))


def stem(name: str) -> str:
    s = re.sub(r"^[\d\s_.\-]+(?=[^\d\s_.\-])", "", Path(name).stem)    # 앞머리 순번(「01-1_」)은 과제 번호가 아니다
    unit = _UNIT.search(s)
    heads = _HEAD.findall(s)
    if unit and heads:
        return f"{heads[-1][1]}#{unit.group(1).lstrip('0')}"

    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)                      # 괄호 속 꾸밈(작업자·메모·서식 번호)
    s = re.sub(r"^[\d\s_.\-]+(?=[^\d\s_.\-])", "", s)                    # 앞머리 순번(「01-1_」·「02-4_」)
    s = re.sub(r"(?<![\d.])\d+\s*차(?!\s*년도)", " ", s)                      # 작업 차수(「4차 합본」의 4차)
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


def latest_key(d: dict) -> tuple:
    """판본 고르기 — 제출·최종 > 수정 차수 > 판 번호 > 고친 시각(rclone 이 원본 시각을 지킨다) > 이름 속 날짜."""
    sub_final, rev, ver, date = version_rank(d["filename"])
    return (sub_final, rev, ver, d.get("mtime", 0), date)


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
        st = f.stat()
        docs.append({"id": len(docs), "filename": f.name, "path": str(rel.parent), "head": "", "abs": str(f),
                     "size": st.st_size, "mtime": int(st.st_mtime)})
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
        card = next((c for c in cards if c.node_id == pid), None)
        surfaces = card.surfaces() if card else set()
        groups = defaultdict(list)
        for d, a in items:
            ext = Path(d["filename"]).suffix.lower().lstrip(".")
            if a.kind not in CORE or ext not in DOC_RANK or EVIDENCE.search(f"{d['path']}/{d['filename']}"):
                continue
            if not is_core(d, a.kind, surfaces):
                continue
            when = a.round or a.year or (programs._PATH_YEAR.findall(d["path"]) or [""])[-1]
            groups[(a.kind, str(when), stem(d["filename"]))].append((d, a))
        chosen = []
        for (kind, when, _s), cands in groups.items():
            best = max(cands, key=lambda t: (latest_key(t[0]),
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
