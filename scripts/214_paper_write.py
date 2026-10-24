#!/usr/bin/env python3
"""논문 원고 쓰기 — docs/paper/논문/*.md 의 절을 논문 문서(웹 한글 기안기, ADR-0072)의 절 필드에 Claude 참여자로 쓴다.

사람·AURA 와 같은 공동 편집 길(app/webhwp_coedit: 임대 잡기 → 칸 글 → 놓기 → 한글 파일 써 넣기)이라 문서를 연 사람은 「Claude」 이름표가 붙은
칸에 글이 차오르는 것을 그대로 본다. 절 사이는 --delay 초 쉰다. 사람이 지금 쥔 칸, 사람이 이미 쓴 칸(--overwrite 가 아니면)은 건너뛰고 알린다.

- 기본(글): 칸 글만 오간다 — 번호 목록 「1) 」, 팁 「※ 참고: 」, 그림 자리 + 「[그림 n-m]」 캡션, 표는 「칸 | 칸」 줄 + 「[표 n-m]」 캡션, 코드는 들여쓰기.
- --rich(서식째): 절 필드 뒤에 문단·표·도식을 파일로 넣고 꾸밈 스타일(단계 목록·팁 상자·그림 자리·캡션·코드)을 입힌다(AURA 의 절 쓰기와 같은 길).
  열린 편집 화면은 저장 알림으로 새 판을 다시 불러온다. 누가 구조 잠금을 쥐고 있으면 쓰지 않는다.
- 서식째 쓰기에서 그림·표 목록(그림-표-목록.md)의 「자료」 가 저장소 그림 파일이면 그림 자리 대신 그 그림을 넣는다(캡션은 아래).
- 그림·표 목차 칸은 원고의 캡션에서 채운다.
- --restamp: 이미 만든 문서의 필드 밖 학과 글(표지 아래 줄·꼬리말)을 바꾼다(앞 판은 .판/, 열린 화면은 새 판). 표지 머리 줄의 학과는 칸이라
  --set 칸:학과=… 로 쓴다.

사용(로컬 시험 서버 자료):
  PYTHONPATH=src python scripts/214_paper_write.py --db <자료>/t.db --dry-run
  PYTHONPATH=src python scripts/214_paper_write.py --db <자료>/t.db --only 1.1,1.2 --delay 2
  PYTHONPATH=src python scripts/214_paper_write.py --db <자료>/t.db --restamp --set 칸:학과=사이버보안과
  PYTHONPATH=src python scripts/214_paper_write.py --db <자료>/t.db --rich --formatted   # 표·그림이 있는 절만 서식째로 다시
운영: --db 를 주지 않으면 data/platform/platform.db(.env.local 의 DB 설정을 따른다), 자료 폴더는 DB 옆.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import paper_hwp as ph  # noqa: E402
from zzaimy.app import webhwp  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402

WHY = {"held": "지금 쓰는 사람이 있다", "human": "사람이 쓴 글이 있다(--overwrite 로 덮어쓴다)", "struct": "구조 잠금을 쥔 사람이 있다",
       "check": "고친 파일이 점검을 통과하지 못했다", "none": "쓴 것이 없다", "fields": "칸 글이 달라질 뻔했다"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/platform/platform.db", help="플랫폼 DB(로컬 시험 서버는 <자료>/t.db)")
    ap.add_argument("--base", default="", help="자료 폴더(기본: DB 가 든 폴더)")
    ap.add_argument("--fid", type=int, default=0, help="논문 문서 id(기본: 설정 paper:hwp_fid)")
    ap.add_argument("--src", default=str(ROOT / "docs" / "paper" / "논문"), help="원고 폴더(*.md, README 제외) 또는 파일 하나")
    ap.add_argument("--only", default="", help="이 번호·제목의 절만(쉼표로) — 예: 1.1,2.3,참고 문헌")
    ap.add_argument("--delay", type=float, default=1.5, help="절 사이 쉬는 초")
    ap.add_argument("--pause", type=float, default=0.4, help="한 절 안에서 글이 차오르는 걸음 사이 초")
    ap.add_argument("--rich", action="store_true", help="서식째 쓰기(문단·표·꾸밈 스타일을 파일로)")
    ap.add_argument("--formatted", action="store_true", help="표·그림·팁·코드·도식·참고 문헌이 있는 절만(--rich 로 다시 쓸 때)")
    ap.add_argument("--overwrite", action="store_true", help="사람이 쓴 칸도 덮어쓴다")
    ap.add_argument("--set", action="append", default=[], metavar="칸=값", help="표지 칸 값(예: 칸:팀명=AURA) — 칸에 글이 있어도 바꾼다")
    ap.add_argument("--dry-run", action="store_true", help="쓸 목록만 보인다")
    ap.add_argument("--restamp", action="store_true", help="필드 밖 학과 글(표지 아래 줄·꼬리말)만 바꾸고(--dept) 원고는 쓰지 않는다(--set 은 함께 쓴다)")
    ap.add_argument("--dept", default="", help=f"--restamp 의 학과 이름(기본 {ph.META['dept']})")
    a = ap.parse_args()

    db_path = Path(a.db)
    db = Database(db_path)
    base = Path(a.base) if a.base else db_path.parent
    fid = a.fid or int((ph.current(db) or {}).get("id") or 0)
    f = webhwp.get(db, fid) if fid else None
    if not f or not Path(f["path"]).exists():
        print("논문 문서가 없다 — 개발 현황 → 논문·설계 문서에서 「논문 문서 만들기」 를 먼저 누른다(또는 --fid).")
        return 2
    if a.restamp:
        dept = a.dept or ph.META["dept"]
        if a.dry_run:
            m = webhwp.meta(db, fid).get("paper") or {}
            old = [x for x in {m.get("dept") or "", ph.PLACEHOLDER_DEPT} if x and x != dept]
            _, n = ph.stamp_dept(Path(f["path"]).read_bytes(), old, dept)
            print(f"문서 {fid}: 필드 밖 학과 글 {n}곳을 「{dept}」 로 바꿀 것(미리 보기)")
        else:
            r = ph.restamp(db, base, fid, dept)
            print(f"문서 {fid}: 학과 「{dept}」 — " + (("바꿈 " + r.get("note", "")) if r.get("changed") else "바꿀 것 없음")
                  if r["ok"] else f"문서 {fid}: 바꾸지 않음 — {WHY.get(r['why'], r['why'])}{' · ' + r['by'] if r.get('by') else ''}")
        if not a.set:
            return 0
    src = Path(a.src)
    files = [] if a.restamp else ([src] if src.is_file() else ph.chapter_files(src))
    if not files and not a.set:
        print(f"원고가 없다: {src}")
        return 2
    texts = [p.read_text(encoding="utf-8") for p in files]
    figures = ph.figure_files((src.parent if src.is_file() else src) / "그림-표-목록.md", ROOT) if a.rich else {}
    p = ph.plan(Path(f["path"]).read_bytes(), texts, rich=a.rich, figures=figures)
    writes = p["writes"]
    if a.only:
        keys = [x.strip() for x in a.only.split(",") if x.strip()]
        writes = [w for w in writes if any(w["field"].startswith(f"절:{k} ") or w["field"] == f"절:{k}" for k in keys)]
    if a.formatted:
        writes = [w for w in writes if set(w.get("blocks", [])) & {"table", "figure", "tip", "code", "diagram"}
                  or w["field"] == "절:참고 문헌"]
    # --set 은 사람이 시킨 값이라 칸에 글이 있어도 바꾼다(지금 누가 쥔 칸은 그래도 건너뛴다)
    extra = [{"field": k.strip(), "kind": "text", "text": v.strip(), "set": True} for k, v in (s.split("=", 1) for s in a.set if "=" in s)]
    lists = [{"field": k, "kind": "text", "text": v} for k, v in p["lists"].items()] if not a.only and files else []
    todo = extra + writes + lists
    pics = sum(len(w.get("pictures") or {}) for w in writes)
    print(f"문서 {fid} 「{Path(f['name']).stem}」 · 원고 {len(files)}개 · 쓸 절 {len(todo)}개 · {'서식째' if a.rich else '글'}"
          + (f" · 넣을 그림 {pics}개" if a.rich else ""))
    for x in p["missing"]:
        print(f"  필드 없음(건너뜀): {x}")
    for x in p["notes"]:
        print(f"  원고 메모라 쓰지 않음: {x}")
    for x in p["chapter_heads"]:
        print(f"  장 머리 글은 쓸 자리가 없어 건너뜀: {x}")
    if a.dry_run:
        for w in todo:
            prev = w["text"].replace("\n", " / ")
            pic = f" · 그림 {len(w['pictures'])}" if w.get("pictures") else ""
            print(f"  {w['field']} · {len(w['text'])}자 · {','.join(w.get('blocks', [])) or '칸'}{pic} · {prev[:90]}")
        return 0
    done, skipped = [], []
    try:
        for i, w in enumerate(todo):
            r = ph.write(db, base, fid, w, overwrite=a.overwrite or bool(w.get("set")), pause=a.pause)
            if r["ok"]:
                done.append(w["field"])
                print(f"  씀: {w['field']}")
            else:
                skipped.append(r)
                print(f"  건너뜀: {w['field']} — {WHY.get(r['why'], r['why'])}{' · ' + webhwp.display_name(db, r['by']) if r.get('by') else ''}")
            if i + 1 < len(todo):
                time.sleep(a.delay)
    finally:
        ph.leave(db, fid)
    print(f"끝: 씀 {len(done)} · 건너뜀 {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
