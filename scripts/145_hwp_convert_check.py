#!/usr/bin/env python3
"""한글 → 워드 변환 회귀 검사 — 문서함의 한글 문서(hwp·hwpx) 전부를 변환해 원본 구조와 대조한다.

문서 하나를 보고 손보는 대신 묶음 전체로 품질을 잰다(사용자 지시 2026-09-25: 올리면 바로 되고 잘못되면 스스로 알아채야).
문서마다:
  글자 수 대조 — 원본 XML 의 글자 수 대 docx 의 글자 수(비율 0.9 미만이면 WARN)
  표 대조     — 원본 표 수 대 docx 표 수(중첩 포함, 다르면 WARN)
  그림 대조   — 원본 그림 수 대 docx 그림 수
  구조 검사   — 붙은 표(독스가 합침), 표 속성 순서, 글 든 가는 열(300트윕 미만), 표 너비 0 → FAIL
결과는 표로, 마지막에 WARN·FAIL 수. --limit 로 건수 제한, --kinds 로 hwp 또는 hwpx 만.

실행(VM): set -a; . .env.local; set +a; env PYTHONPATH=src .venv/bin/python scripts/145_hwp_convert_check.py [--limit 30] [--kinds hwpx]
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402


def _source_counts_hwpx(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        text = 0
        tables = 0
        pics = 0
        for n in zf.namelist():
            if re.search(r"Contents/section\d+\.xml$", n):
                s = zf.read(n).decode("utf-8", "replace")
                text += sum(len(t) for t in re.findall(r"<hp:t>([^<]*)</hp:t>", s))
                tables += s.count("<hp:tbl ")
                pics += s.count("<hp:pic ")
    return {"text": text, "tables": tables, "pics": pics}


def _source_counts_hwp5(xml_path: Path) -> dict:
    s = xml_path.read_text(encoding="utf-8", errors="replace")
    return {"text": sum(len(t) for t in re.findall(r"<Text [^>]*>([^<]*)</Text>", s)),
            "tables": s.count("<TableControl "), "pics": s.count("<ShapePicture ")}


def _docx_counts(data: bytes) -> dict:
    from docx import Document
    from docx.oxml.ns import qn

    from zzaimy.ingest.hwpx_docx import _TBLPR_ORDER

    d = Document(io.BytesIO(data))
    body = d.element.body
    text = sum(len(t.text or "") for t in body.iter(qn("w:t")))
    tables = sum(1 for _ in body.iter(qn("w:tbl")))
    pics = sum(1 for _ in body.iter(qn("w:drawing")))
    fails: list[str] = []
    # 붙은 표
    kids = [k.tag.split("}")[1] for k in body]
    if any(a == "tbl" and b == "tbl" for a, b in zip(kids, kids[1:])):
        fails.append("붙은 표")
    for tc in body.iter(qn("w:tc")):
        ks = [k.tag.split("}")[1] for k in tc if k.tag.split("}")[1] in ("tbl", "p")]
        if any(a == "tbl" and b == "tbl" for a, b in zip(ks, ks[1:])):
            fails.append("셀 안 붙은 표"); break
    for tbl in body.iter(qn("w:tbl")):
        tblPr = tbl.find(qn("w:tblPr"))
        names = [c.tag.split("}")[1] for c in tblPr] if tblPr is not None else []
        ranks = [_TBLPR_ORDER.index(n) if n in _TBLPR_ORDER else 99 for n in names]
        if ranks != sorted(ranks) or len(names) != len(set(names)):
            fails.append("표 속성 순서"); break
        tw = tblPr.find(qn("w:tblW")) if tblPr is not None else None
        if tw is None or int(float(tw.get(qn("w:w")) or 0)) <= 0:
            fails.append("표 너비 0"); break
        grid = [int(float(g.get(qn("w:w")) or 0)) for g in tbl.find(qn("w:tblGrid")).findall(qn("w:gridCol"))]
        tiny = {i for i, w in enumerate(grid) if w < 300}
        if tiny:
            for tr in tbl.findall(qn("w:tr")):
                col = 0
                for tc in tr.findall(qn("w:tc")):
                    tcPr = tc.find(qn("w:tcPr")); gs = tcPr.find(qn("w:gridSpan")) if tcPr is not None else None
                    span = int(gs.get(qn("w:val"))) if gs is not None else 1
                    own = "".join(t.text or "" for p in tc.findall(qn("w:p")) for t in p.iter(qn("w:t"))).strip()
                    if col in tiny and span == 1 and len(own) > 5:
                        fails.append("가는 열에 글"); break
                    col += span
                if "가는 열에 글" in fails:
                    break
    return {"text": text, "tables": tables, "pics": pics, "fails": sorted(set(fails))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--kinds", default="hwp,hwpx")
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    db = Database(Path(args.db))
    kinds = {"." + k.strip() for k in args.kinds.split(",") if k.strip()}
    docs = [d for d in db.list_documents_all() if Path(d.get("stored_path") or "").suffix.lower() in kinds] if hasattr(db, "list_documents_all") else []
    if not docs:
        with db._conn() as conn:
            rows = conn.execute("SELECT id, filename, stored_path, status FROM documents ORDER BY id").fetchall()
        docs = [dict(r) for r in rows if Path(r["stored_path"] or "").suffix.lower() in kinds]
    if args.ids:
        want = {int(x) for x in args.ids.split(",")}
        docs = [d for d in docs if d["id"] in want]
    if args.limit:
        docs = docs[: args.limit]
    from zzaimy.ingest import hwp5_docx, hwpx_docx

    warn = fail = 0
    print(f"{'id':>4} {'형식':4} {'글자':>7} {'표':>7} {'그림':>5} {'초':>4}  판정  파일")
    for d in docs:
        path = Path(d["stored_path"])
        if not path.exists():
            continue
        t0 = time.time()
        try:
            if path.suffix.lower() == ".hwpx":
                src = _source_counts_hwpx(path)
                data, _ = hwpx_docx.convert(path)
            else:
                xml = hwp5_docx.dump_xml(path)
                src = _source_counts_hwp5(xml)
                data, _ = hwp5_docx.convert_xml(xml)
            out = _docx_counts(data)
        except Exception as e:
            fail += 1
            print(f"{d['id']:>4} {path.suffix[1:]:4} {'-':>7} {'-':>7} {'-':>5} {time.time() - t0:4.0f}  FAIL  {d['filename'][:40]} — {type(e).__name__}: {str(e)[:60]}")
            continue
        ratio = out["text"] / src["text"] if src["text"] else 1.0
        level = "PASS"
        why = []
        if out["fails"]:
            level = "FAIL"; why += out["fails"]
        elif ratio < 0.9 or ratio > 1.3:
            level = "WARN"; why.append(f"글자 {ratio:.2f}")
        elif out["tables"] < src["tables"]:
            level = "WARN"; why.append("표 부족")
        elif out["pics"] < src["pics"]:
            level = "WARN"; why.append("그림 부족")
        warn += level == "WARN"; fail += level == "FAIL"
        print(f"{d['id']:>4} {path.suffix[1:]:4} {out['text']:>3}/{src['text']:<3} {out['tables']:>3}/{src['tables']:<3} {out['pics']:>2}/{src['pics']:<2} {time.time() - t0:4.0f}  {level}  {d['filename'][:40]} {' '.join(why)}")
    print(f"문서 {len(docs)}건 · WARN {warn} · FAIL {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
