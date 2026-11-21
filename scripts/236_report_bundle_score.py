"""결과보고서 시험 채점 — 결과 작업본(hwpx)의 표를 truth.json(235 가 만든 정답 집계)과 대조한다. 모델 채점 없음, 모두 기계 대조.

  python scripts/236_report_bundle_score.py <묶음> <runs/시각/result.hwpx>
보는 것: 월별 참여 인원, 분야별 활동 수·참여 인원, 성과지표 실적, 비목별 집행액, 총 집행액·집행률, 작년(2025) 값이 남은 수.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path


def _text(x: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<hp:lineBreak\s*/>", " ", x))).strip()


def tables(hwpx: Path) -> tuple[list[list[str]], str]:
    z = zipfile.ZipFile(hwpx)
    rows, full = [], []
    for n in sorted(x for x in z.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
        xml = z.read(n).decode("utf-8")
        full.append(_text("".join(re.findall(r"<hp:t>(?:(?!</hp:t>).)*?</hp:t>", xml, re.S))))
        for tr in re.findall(r"<hp:tr\b.*?</hp:tr>", xml, re.S):
            cells = [_text("".join(re.findall(r"<hp:t>(?:(?!</hp:t>).)*?</hp:t>", tc, re.S)))
                     for tc in re.findall(r"<hp:tc\b.*?</hp:tc>", tr, re.S)]
            rows.append(cells)
    return rows, " ".join(full)


def _nums(s: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def score(set_dir: Path, result: Path) -> dict:
    t = json.loads((set_dir / "truth.json").read_text(encoding="utf-8"))
    rows, full = tables(result)
    res = {}

    def row_for(pred):
        return next((r for r in rows if r and pred(r[0])), None)

    ok = 0
    for m, v in t["월별"].items():
        r = row_for(lambda c, m=m: re.fullmatch(rf"0?{m}\s*월", c.strip()) is not None)
        ok += bool(r and str(v["참여 인원"]) in _nums(" ".join(r[1:])))
    res["월별 참여 인원"] = f"{ok}/{len(t['월별'])}"
    ok = 0
    for f, v in t["분야별"].items():
        r = row_for(lambda c, f=f: c.strip() == f)
        ok += bool(r and str(v["참여 인원"]) in _nums(" ".join(r[1:])) and str(v["행사"]) in _nums(" ".join(r[1:])))
    res["분야별 활동 수·인원"] = f"{ok}/{len(t['분야별'])}"
    ok = 0
    for k, v in t["성과지표"].items():
        r = row_for(lambda c, k=k: c.strip().startswith(k.split("(")[0]))
        ok += bool(r and str(v["실적"]) in _nums(" ".join(r[1:])))
    res["성과지표 실적"] = f"{ok}/{len(t['성과지표'])}"
    ok = 0
    for b, v in t["비목별 집행액"].items():
        r = row_for(lambda c, b=b: c.strip() == b)
        ok += bool(r and str(v) in _nums(" ".join(r[1:])))
    res["비목별 집행액"] = f"{ok}/{len(t['비목별 집행액'])}"
    res["총 집행액"] = str(t["총 집행액"]) in _nums(full)
    res["집행률"] = str(t["집행률(%)"]) in _nums(full)
    # 올해 정답과 우연히 같은 수(취업 연계 31명·달성률 83.3%)는 잔여로 세지 않는다
    legit = {str(x) for x in [t["총 집행액"], t["집행률(%)"]] + [v for d in t["월별"].values() for v in d.values()]
             + [v for d in t["분야별"].values() for v in d.values()] + [v["실적"] for v in t["성과지표"].values()]
             + list(t["비목별 집행액"].values())}
    legit |= {f"{round(v['실적'] / v['목표'] * 100, 1)}" for v in t["성과지표"].values()}
    toks = _nums(re.sub(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?", " ", full))     # 날짜(2026. 5. 21.)의 낱수는 잔여가 아니다

    def present(v: str) -> bool:
        # 수만 든 값은 수 낱개로 대조한다 — 「200」 이 「28,200,000」 안에, 「21」 이 「21.3」 에 걸려 잔여로 세던 것(11-20 2차)
        if re.fullmatch(r"[\d,.]+", v):
            return v.replace(",", "") in toks
        return v in full

    left = [v for v in t["작년 값"] if present(v) and v.replace(",", "") not in legit]
    res["작년 값 남음"] = len(left)
    res["작년 값 예"] = left[:8]
    return res


if __name__ == "__main__":
    print(json.dumps(score(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=False, indent=1))
