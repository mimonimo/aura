"""결과보고서 시험 채점(일반) — truth.json 의 「tables」(표마다 첫 열 이름 → 그 행에 있어야 할 수)로 결과 작업본(hwpx)의 행을 찾아 수를 대조한다.
사업 이름·표 이름에 묶이지 않는다 — 237(묶음 B)처럼 짜임이 다른 사업도 같은 채점으로 본다. 모델 채점 없음, 모두 기계 대조.

  python scripts/238_report_table_score.py <묶음> <runs/시각/result.hwpx>
보는 것: 표마다 맞은 행 수, 「같은 이름 행 합산」(활동마다 한 행으로 쓴 판 — 같은 첫 열 이름 행의 첫 수를 더해 본다, 보조),
본문 값(총 집행액·집행률 등), 작년 값이 남은 수.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("s236", Path(__file__).with_name("236_report_bundle_score.py"))
_s236 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s236)
tables, _nums = _s236.tables, _s236._nums


def _key(s: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"\s*\([^)]*\)\s*$", "", s or ""))


def _match(rows: list[list[str]], i: int, vals) -> bool:
    """i 번째 행이 정답 값을 담았는지 — 목록이면 행 어디든, {열 이름: 값} 이면 위쪽 머리줄에서 그 열을 찾아 그 칸만."""
    r = rows[i]
    if not isinstance(vals, dict):
        return all(str(v).replace(",", "") in _nums(" ".join(r[1:])) for v in vals)
    for col, v in vals.items():
        head = next((h for h in reversed(rows[:i]) if any(_key(c).startswith(_key(col)) for c in h)), None)
        j = next((j for j, c in enumerate(head) if _key(c).startswith(_key(col))), None) if head else None
        if j is None or j >= len(r) or str(v).replace(",", "") not in _nums(r[j]):
            return False
    return True


def score(set_dir: Path, result: Path) -> dict:
    t = json.loads((set_dir / "truth.json").read_text(encoding="utf-8"))
    rows, full = tables(result)
    res = {}
    for name, want in t["tables"].items():
        ok = 0
        for label, vals in want.items():
            k = _key(label)
            ok += any(_match(rows, i, vals) for i, r in enumerate(rows) if r and _key(r[0]) == k)
        res[name] = f"{ok}/{len(want)}"
        # 같은 첫 열 이름 행이 여럿이면 첫 수 열을 더해 본다(활동마다 한 행) — 값과 꼴을 따로 보려고
        if any(isinstance(v, list) and len(v) == 1 for v in want.values()):
            ok2 = 0
            for label, vals in want.items():
                hit = [r for r in rows if r and _key(r[0]) == _key(label)]
                if len(hit) >= 2 and isinstance(vals, list) and len(vals) == 1:
                    tot = 0
                    for r in hit:
                        m = next((re.search(r"\d[\d,]*", c) for c in r[1:] if re.search(r"\d", c)), None)
                        tot += int(m.group(0).replace(",", "")) if m else 0
                    ok2 += str(tot) == str(vals[0]).replace(",", "")
                else:
                    ok2 += any(_match(rows, i, vals) for i, r in enumerate(rows) if r and _key(r[0]) == _key(label))
            res[name + "(같은 이름 행 합산)"] = f"{ok2}/{len(want)}"
    for k, v in (t.get("본문") or {}).items():
        res[k] = str(v).replace(",", "") in _nums(full)
    legit = {str(x).replace(",", "") for x in t.get("정답 수(잔여 판단에서 뺌)") or []}
    toks = _nums(re.sub(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?|\d{4}년\s*\d{1,2}월\s*\d{1,2}일", " ", full))

    def present(v: str) -> bool:
        if re.fullmatch(r"[\d,.]+", v):
            return v.replace(",", "") in toks
        return v in full

    left = [v for v in t.get("작년 값") or [] if present(v) and v.replace(",", "") not in legit]
    res["작년 값 남음"] = len(left)
    res["작년 값 예"] = left[:8]
    return res


if __name__ == "__main__":
    print(json.dumps(score(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=False, indent=1))
