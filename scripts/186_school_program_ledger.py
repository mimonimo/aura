#!/usr/bin/env python3
"""교내 재정지원사업 현황(연도별 엑셀) → 외부 확인 장부(kg_external.json)의 사업 이름·별칭·연도 보강.

학교가 해마다 내는 「재정지원사업 수혜 실적」 표는 우리 대학이 그해 진행한 사업의 공식 목록이다. 사업명·세부 과제명·
지원기관·주관기관·사업 기간·업무 부서를 읽어(책임자·담당자 이름은 읽지 않는다)
- 장부의 사업 항목과 이어지면: 그 항목의 별칭(terms)에 학교 표의 사업명·부서 약칭을 더하고 연도·출처를 「school」 에 적는다
- 이어지는 항목이 없으면: 교내 장부 항목으로 새로 만든다(category 「교내 장부」)
그래프를 다시 만들면(scripts/157) 사업 카드의 이름 묶음(program_names.group_names)이 이 별칭으로 같은 사업을 하나로 모은다.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/186_school_program_ledger.py data/inbox/finance_ledger/*.xlsx   # 미리 보기
          ... --apply   (장부를 바꾸기 전에 data/platform/history/ 에 사본)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

HEAD = {"name": ("사업명",), "sub": ("세부 사업", "세부사업", "과제명"), "ministry": ("지원기관",), "agency": ("주관",),
        "period": ("총사업기간",), "dept": ("기타사항", "업무진행부서"), "status": ("구분",)}
_YEAR = re.compile(r"(20\d{2})\s*학년도")
_LEAD_YEAR = re.compile(r"^\s*(?:19|20)\d{2}\s*(?:년도|학년도|년)?\s*(?:상반기|하반기)?\s*")
_DEPT_TAIL = re.compile(r"(사업단|사업팀|센터|추진단|운영단|지원단|단)$")


def _norm(t: str) -> str:
    return re.sub(r"[\s·.\-_()（）\[\]]", "", str(t or "")).upper()


def read_sheet(path: Path) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [[("" if c is None else str(c).strip()) for c in r] for r in ws.iter_rows(values_only=True)]
        year = next((int(m.group(1)) for r in rows[:4] for c in r if (m := _YEAR.search(c))), None)
        hi = next((i for i, r in enumerate(rows[:12]) if any("사업명" in c for c in r)), None)
        if hi is None or year is None:
            continue
        head = rows[hi]
        col = {}
        for key, words in HEAD.items():
            for j, c in enumerate(head):
                if any(w in c.replace("\n", " ") for w in words) and key not in col:
                    col[key] = j
        for r in rows[hi + 1:]:
            name = r[col["name"]] if "name" in col and col["name"] < len(r) else ""
            name = _LEAD_YEAR.sub("", " ".join(name.split())).strip()       # 「2022년 …사업」 — 연도는 이름이 아니다
            if not name or len(name) < 3 or name.startswith(("합계", "계", "소계", "※")):
                continue
            get = lambda k: " ".join(r[col[k]].split()) if k in col and col[k] < len(r) else ""
            out.append({"year": year, "name": name, "sub": get("sub"), "ministry": get("ministry"), "agency": get("agency"),
                        "period": get("period"), "dept": get("dept"), "status": get("status"), "file": path.name})
    return out


def match(entries: list[dict], name: str) -> int | None:
    """학교 표의 사업명 → 장부 항목 번호. 장부의 이름·별칭(3자 이상)이 학교 이름에 들어 있거나 그 반대면 이어진다 —
    가장 긴 겹침을 고른다. 겹침이 없으면 None."""
    n = _norm(name)
    best = (0, None)
    for i, e in enumerate(entries):
        for t in [e.get("name") or "", *(e.get("terms") or [])]:
            k = _norm(re.sub(r"\(.*?\)", "", t)) or _norm(t)
            if len(k) >= 3 and (k in n or (len(n) >= 6 and n in k)):
                if len(k) > best[0]:
                    best = (len(k), i)
    return best[1]


def dept_alias(dept: str) -> str:
    """업무 부서 이름에서 사업 약칭 — 「LINC3.0사업단」 → 「LINC3.0」. 부서가 사업 이름을 담을 때만(영문·숫자 3자 이상)."""
    d = _DEPT_TAIL.sub("", (dept or "").split(",")[0].strip())
    return d if re.search(r"[A-Za-z]{3,}", d) and len(d) <= 20 else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--ledger", default=str(ROOT / "data/platform/kg_external.json"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    rows = [r for f in args.files for r in read_sheet(Path(f))]
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    entries = ledger.setdefault("programs", [])
    groups: dict[str, dict] = {}
    for r in rows:
        i = match(entries, r["name"])
        key = f"e{i}" if i is not None else "n:" + _norm(r["name"])
        g = groups.setdefault(key, {"entry": i, "names": set(), "years": set(), "subs": set(), "depts": set(),
                                    "ministry": set(), "agency": set(), "period": set(), "status": set()})
        g["names"].add(r["name"]); g["years"].add(r["year"]); g["subs"].add(r["sub"]); g["depts"].add(r["dept"])
        g["ministry"].add(r["ministry"]); g["agency"].add(r["agency"]); g["period"].add(r["period"]); g["status"].add(r["status"])
    print(f"학교 표 {len(rows)}줄 · 사업 {len(groups)}개 (장부 항목과 이어짐 {sum(g['entry'] is not None for g in groups.values())})")
    added_terms = 0
    new_entries = []
    for key, g in sorted(groups.items(), key=lambda kv: (kv[1]["entry"] is None, sorted(kv[1]["names"])[0])):
        names = sorted(n for n in g["names"] if n)
        aliases = sorted({a for d in g["depts"] if (a := dept_alias(d))})
        school = {"years": sorted(g["years"]), "names": names, "subs": sorted(s for s in g["subs"] if s)[:8],
                  "depts": sorted(d for d in g["depts"] if d), "source": "교내 재정지원사업 현황(2022~2026, 연도별 엑셀)",
                  "checked": time.strftime("%Y-%m-%d")}
        if g["entry"] is not None:
            e = entries[g["entry"]]
            have = {_norm(t) for t in [e.get("name") or "", *(e.get("terms") or [])]}
            more = [t for t in names + aliases if _norm(t) not in have]
            print(f"  이어짐  {e.get('name')}  ← {', '.join(names)[:90]}" + (f"  · 별칭 더함 {more}" if more else "")
                  + f"  · {school['years']}")
            if args.apply:
                e["terms"] = list(dict.fromkeys([*(e.get("terms") or []), *more]))
                e["school"] = school
            added_terms += len(more)
        else:
            ministry = sorted(m for m in g["ministry"] if m)
            agency = sorted(a for a in g["agency"] if a)
            ne = {"name": names[0], "terms": list(dict.fromkeys(names + aliases)), "category": "교내 장부",
                  "ministry": ", ".join(ministry), "agency": ", ".join(agency),
                  "period": sorted(p for p in g["period"] if p)[0] if any(g["period"]) else "",
                  "sources": [school["source"]], "checked": school["checked"], "school": school,
                  "note": "교내 재정지원사업 현황 표에서 온 항목 — 외부 출처 확인 전"}
            print(f"  새 항목  {names[0]}  · {school['years']} · {', '.join(ministry)[:30]}")
            new_entries.append(ne)
    print(f"별칭 {added_terms}개 더함 · 새 항목 {len(new_entries)}개")
    if not args.apply:
        print("미리 보기입니다 — --apply 로 장부에 씁니다")
        return 0
    hist = Path(args.ledger).parent / "history"
    hist.mkdir(exist_ok=True)
    shutil.copy2(args.ledger, hist / f"kg_external.{time.strftime('%Y%m%d%H%M%S')}.json")
    entries.extend(new_entries)
    Path(args.ledger).write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"썼다 — {args.ledger} (사본 history/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
