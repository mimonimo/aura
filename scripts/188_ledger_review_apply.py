#!/usr/bin/env python3
"""검토를 마친 장부 변경(configs/ledger_reviews/*.json)을 외부 확인 장부(kg_external.json)에 적용한다.

라이브러리 「사업별로 보기」의 「장부에 없는 이름 (확인 필요)」을 사람이(또는 Claude 가 외부 검색으로) 확인한 결과를
장부에 옮기는 도구다. 결정마다 근거(why·sources·note)를 함께 적고, 사례 규칙을 코드에 넣지 않는다.
- add_terms: 기존 항목의 별칭(terms)에 더한다 — 같은 사업의 다른 표기·잘린 이름
- add_programs: 새 사업 항목 — 공개 출처(sources)가 없으면 넣지 않는다
- not_programs: 「사업 아님」 항목 — 사업 목록 나무가 「사업 이름 아닌 묶음」으로 뺀다(app.program_tree)
같은 이름의 항목·별칭이 이미 있으면 건너뛴다(여러 번 돌려도 같다).

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/188_ledger_review_apply.py configs/ledger_reviews/2026-10-13-program-names.json   # 미리 보기
          ... --apply   (장부를 바꾸기 전에 data/platform/history/ 에 사본)
사업 목록 나무는 장부를 바로 읽는다. 그래프의 이름 묶음은 다음 재구축(scripts/157) 때 따른다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.program_names import group_key  # noqa: E402


def apply(ledger: dict, review: dict) -> list[str]:
    """review 를 ledger 에 적용하고 한 일을 줄로 돌려준다."""
    entries = ledger.setdefault("programs", [])
    checked = review.get("checked") or time.strftime("%Y-%m-%d")
    by_name = {e.get("name"): e for e in entries}
    known = {group_key(t) for e in entries for t in [e.get("name") or "", *(e.get("terms") or [])] if t}
    log: list[str] = []
    for ch in review.get("add_terms") or []:
        e = by_name.get(ch["entry"])
        if e is None:
            log.append(f"항목 없음(건너뜀): {ch['entry']}")
            continue
        have = {group_key(t) for t in [e.get("name") or "", *(e.get("terms") or [])]}
        new = [t for t in ch.get("terms") or [] if group_key(t) not in have]
        if new:
            e["terms"] = [*(e.get("terms") or []), *new]
            e.setdefault("reviews", []).append({"checked": checked, "terms": new, "why": ch.get("why", "")})
            if ch.get("sources"):
                e["sources"] = list(dict.fromkeys([*(e.get("sources") or []), *ch["sources"]]))
            known |= {group_key(t) for t in new}
            log.append(f"별칭  {e['name']}  += {new}")
    for kind, items in (("program", review.get("add_programs") or []), ("not", review.get("not_programs") or [])):
        for p in items:
            if p["name"] in by_name or group_key(p["name"]) in known:
                log.append(f"이미 있음(건너뜀): {p['name']}")
                continue
            if kind == "program" and not p.get("sources"):
                log.append(f"출처 없음(건너뜀): {p['name']}")
                continue
            e = {**p, "checked": checked}
            if kind == "not":
                e["category"] = "사업 아님"
            entries.append(e)
            by_name[e["name"]] = e
            known |= {group_key(t) for t in [e["name"], *(e.get("terms") or [])]}
            log.append(("사업 아님 " if kind == "not" else "새 사업  ") + f"{e['name']} ({e.get('ministry') or '-'})")
    if review.get("note"):
        ledger["note"] = (ledger.get("note") or "") + f" {checked}: {review['note']}"
    return log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("review")
    ap.add_argument("--ledger", default=str(ROOT / "data/platform/kg_external.json"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    log = apply(ledger, review)
    for line in log:
        print(" ", line)
    print(f"변경 {sum(1 for x in log if '건너뜀' not in x)}건 · 건너뜀 {sum(1 for x in log if '건너뜀' in x)}건")
    if args.apply:
        hist = Path(args.ledger).parent / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(args.ledger, hist / f"kg_external.{time.strftime('%Y%m%d%H%M%S')}.json")
        tmp = Path(args.ledger).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(args.ledger)
        print(f"썼다 — {args.ledger} (사본 history/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
