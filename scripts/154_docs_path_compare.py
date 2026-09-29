#!/usr/bin/env python3
"""한글 → 구글 독스 변환 경로 견주기 — 어느 길이 독스에서 원본에 가장 가깝나(2026-09-30 실험).

경로(문서마다):
  A   지금 경로: 우리 변환기 → docx → 독스 변환 업로드(gdrive_files.bytes_for_view 그대로)
  B   kordoc 마크다운(기본 출력, 병합 셀 표는 kordoc 이 HTML 로 낸다) → text/markdown 으로 올려 독스 변환
  B2  kordoc 마크다운(--html-tables) → markdown-it 으로 HTML 렌더(표 테두리 CSS 만 덧붙임) → text/html 로 올려 독스 변환
      (hwp 는 --inline-images 로 그림을 data URI 로 넣는다 — kordoc 이 hwp5 에서만 지원)

잣대(독스 API documents.get + 드라이브 PDF 내보내기):
  쪽수(독스 PDF) 대 원본 쪽수 · 표 수 · 병합 셀(rowSpan/columnSpan>1) · 제목 문단(HEADING_*/TITLE) · 그림(inlineObjects)
  · 글자 덮기(kordoc 마크다운의 줄·칸을 정답 글로 보고, 정규화한 조각이 독스 본문에 들어 있는 비율)
확인용 쪽 그림은 --pages 쪽을 pdftoppm 으로 /tmp/pc/<doc>/<path>-<p>.png 에 남긴다.
올린 파일은 모두 드라이브 ZZAIMY/_변환실험 에 남긴다(지우지 않는다).

  실행(VM): set -a; . ./.env.local; set +a
            env PYTHONPATH=src .venv/bin/python scripts/154_docs_path_compare.py
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "src" / "zzaimy").is_dir() and (Path.cwd() / "src" / "zzaimy").is_dir():
    ROOT = Path.cwd()                                  # /tmp 에 복사해 저장소 폴더에서 돌릴 때
sys.path.insert(0, str(ROOT / "src"))

DOCS = [  # (별칭, 반입 폴더 안 경로 조각, 원본 쪽수 — docs/dev-now.md ⑬⑭ 의 알려진 쪽수)
    ("작성서식", "국고/2026-국고-0001", 62),
    ("사업계획서", "국고/2026-국고-0002", 73),
    ("평가편람", "기준/2026-기준-0003", 22),
]
GDOC = "application/vnd.google-apps.document"
KORDOC = Path.home() / "opt/kordoc/node_modules/kordoc/dist/cli.js"
CSS = ("table{border-collapse:collapse}td,th{border:1px solid #000;padding:2px 4px;vertical-align:top}"
       "th{background:#e8e8e8}body{font-family:'Malgun Gothic',sans-serif}")


def kordoc_md(src: Path, out: Path, *opts: str) -> str:
    env = dict(os.environ, PATH=f"{Path.home()}/opt/node/bin:" + os.environ.get("PATH", ""))
    subprocess.run(["node", str(KORDOC), str(src), "-o", str(out), *opts], check=True, capture_output=True, env=env, timeout=900)
    return out.read_text(encoding="utf-8")


def md_to_html(md: str, title: str) -> str:
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": True}).enable("table").render(md)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{_html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s)


def truth_units(md: str) -> list[str]:
    """kordoc 마크다운에서 정답 글 조각(줄·표 칸·<br> 조각)을 뽑는다. 그림 data URI·표 구분줄은 뺀다."""
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
    md = re.sub(r"<br\s*/?>", "\n", md)
    md = re.sub(r"</t[dh]>|<t[dh][^>]*>|</?tr>|</?table>|</?thead>|</?tbody>", "\n", md)
    md = re.sub(r"<[^>]+>", " ", md)
    md = _html.unescape(md)
    units: set[str] = set()
    for line in md.splitlines():
        for part in line.split("|"):
            p = re.sub(r"^\s*(#+|[-*+]|\d+[.)])\s+", "", part)      # 목록 번호는 독스에서 글이 아닌 번호 매기기가 된다
            n = _norm(p)
            if len(n) >= 4 and not re.fullmatch(r"[-:]+", part.strip()):
                units.add(n)
    return sorted(units)


def walk_doc(doc: dict) -> dict:
    """documents.get 결과에서 표·병합 칸·제목·본문 글을 센다(표 안의 표도)."""
    st = {"tables": 0, "merged": 0, "cells": 0, "headings": 0, "paras": 0, "text": []}

    def content(items):
        for el in items or []:
            if "paragraph" in el:
                p = el["paragraph"]
                st["paras"] += 1
                if (p.get("paragraphStyle", {}).get("namedStyleType", "") or "").startswith(("HEADING", "TITLE")):
                    st["headings"] += 1
                for r in p.get("elements", []):
                    st["text"].append(r.get("textRun", {}).get("content", ""))
            elif "table" in el:
                st["tables"] += 1
                for row in el["table"].get("tableRows", []):
                    for c in row.get("tableCells", []):
                        st["cells"] += 1
                        cs = c.get("tableCellStyle", {})
                        if cs.get("rowSpan", 1) > 1 or cs.get("columnSpan", 1) > 1:
                            st["merged"] += 1
                        content(c.get("content"))
                        st["text"].append("\n")
            elif "tableOfContents" in el:
                content(el["tableOfContents"].get("content"))

    content(doc.get("body", {}).get("content"))
    st["images"] = len(doc.get("inlineObjects", {}) or {}) + len(doc.get("positionedObjects", {}) or {})
    st["text"] = "".join(st["text"])
    return st


def export_pdf(acct, fid, http, headers) -> bytes:
    from zzaimy.ingest import gdrive

    r = http.get(f"{gdrive.API}/files/{fid}/export", headers=headers(), params={"mimeType": "application/pdf"})
    if r.status_code == 200:
        return r.content
    # files.export 는 10MB 한도가 있다 — exportLinks(문서 편집기 내보내기)로 한 번 더
    m = http.get(f"{gdrive.API}/files/{fid}", headers=headers(), params={"fields": "exportLinks"}).json()
    link = (m.get("exportLinks") or {}).get("application/pdf")
    if not link:
        raise RuntimeError(f"PDF 내보내기 실패({r.status_code}): {r.text[:120]}")
    r2 = http.get(link, headers=headers(), follow_redirects=True)
    r2.raise_for_status()
    return r2.content


def pdf_pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT / "data/platform/documents/반입/2026"))
    ap.add_argument("--only", default="", help="별칭(쉼표)")
    ap.add_argument("--paths", default="A,B,B2")
    ap.add_argument("--pages", default="1,3,8", help="확인용 PNG 쪽(쉼표)")
    ap.add_argument("--work", default="/tmp/pc")
    ap.add_argument("--json", default=str(ROOT / "data/eval/docs_path_compare.json"))
    ap.add_argument("--remeasure", action="store_true", help="올리지 않고 --json 에 적힌 독스를 다시 잰다(잣대만 고쳤을 때)")
    args = ap.parse_args()
    import httpx

    from zzaimy.ingest import gdocs, gdrive, gdrive_files

    acct = gdrive.list_accounts()[0]
    acct = acct.get("email") if isinstance(acct, dict) else acct
    http = httpx.Client(timeout=httpx.Timeout(900, connect=30))
    headers = lambda: gdrive_files._headers(acct, http)  # noqa: E731
    folder = gdrive_files.ensure_folder(acct, ["ZZAIMY", "_변환실험"], http=http)
    stamp = datetime.now().strftime("%m%d-%H%M")
    want_paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    prev = {}
    if args.remeasure:
        prev = {(r["doc"], r["path"]): r for r in json.loads(Path(args.json).read_text(encoding="utf-8"))["rows"] if r.get("id")}
    rows = []
    for alias, part, orig_pages in DOCS:
        if only and alias not in only:
            continue
        dirs = [d for d in Path(args.base).glob(part.split("/")[0] + "/*") if d.name.startswith(part.split("/")[1])]
        src = next((f for d in dirs for f in sorted(d.glob("원본.hwp*"))), None)
        if not src:
            print(alias, "원본 없음"); continue
        work = Path(args.work) / alias
        work.mkdir(parents=True, exist_ok=True)
        md = kordoc_md(src, work / "k.md")
        truth = truth_units(md)
        for path in want_paths:
            t0 = time.time()
            row = {"doc": alias, "path": path, "src_ext": src.suffix, "orig_pages": orig_pages, "truth_units": len(truth)}
            try:
                if (alias, path) in prev:
                    old = prev[(alias, path)]
                    up = {"id": old["id"], "url": old["url"], "mime": old["mime"]}
                    row.update({k: old[k] for k in ("upload_bytes", "convert_s", "upload_s") if k in old})
                    data = None
                elif path == "A":
                    data, _name, mime, target = gdrive_files.bytes_for_view(None, {"stored_path": str(src), "filename": src.name})
                    name = f"{alias} A-docx {stamp}" + Path(_name).suffix
                elif path == "B":
                    data, mime, target = md.encode("utf-8"), "text/markdown", GDOC
                    name = f"{alias} B-md {stamp}.md"
                elif path == "B2":
                    opts = ["--html-tables"] + (["--inline-images"] if src.suffix.lower() == ".hwp" else [])
                    md2 = kordoc_md(src, work / "k_html.md", *opts)
                    data, mime, target = md_to_html(md2, alias).encode("utf-8"), "text/html", GDOC
                    name = f"{alias} B2-html {stamp}.html"
                else:
                    continue
                if data is not None:
                    row["upload_bytes"] = len(data)
                    row["convert_s"] = round(time.time() - t0, 1)
                    up = gdrive_files.upload_file(acct, data, name, mime, folder, convert_to=target, http=http, reuse=False)
                    row["upload_s"] = round(time.time() - t0 - row["convert_s"], 1)
                row["id"], row["url"], row["mime"] = up["id"], up["url"], up["mime"]
                if up["mime"] != GDOC:
                    row["error"] = f"독스로 변환되지 않음({up['mime']})"
                    rows.append(row); print(row); continue
                d = http.get(f"{gdocs.DOCS_API}/{up['id']}", headers=headers()).json()
                st = walk_doc(d)
                pdf = work / f"{path}.pdf"
                pdf.write_bytes(export_pdf(acct, up["id"], http, headers))
                row["pages"] = pdf_pages(pdf)
                doc_norm = _norm(st.pop("text"))
                hit = sum(1 for u in truth if u in doc_norm)
                row.update(st)
                row["coverage"] = round(hit / max(len(truth), 1), 4)
                row["missing_sample"] = [u[:40] for u in truth if u not in doc_norm][:8]
                for p in [int(x) for x in args.pages.split(",") if x.strip()]:
                    subprocess.run(["pdftoppm", "-r", "60", "-png", "-f", str(p), "-l", str(p), "-singlefile",
                                    str(pdf), str(work / f"{path}-{p}")], capture_output=True)
            except Exception as e:  # 한 경로가 실패해도 다른 경로는 잰다
                row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            rows.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "missing_sample"}, ensure_ascii=False))
    print()
    print(f"{'문서':<8}{'경로':<4}{'쪽(원본)':>10}{'표':>5}{'병합':>6}{'제목':>5}{'그림':>5}{'덮기':>7}  비고")
    for r in rows:
        if r.get("error"):
            print(f"{r['doc']:<8}{r['path']:<4}  오류: {r['error']}")
            continue
        print(f"{r['doc']:<8}{r['path']:<4}{r['pages']:>5}({r['orig_pages']:>3}){r['tables']:>5}{r['merged']:>6}"
              f"{r['headings']:>5}{r['images']:>5}{r['coverage']*100:>6.1f}%  {r['url']}")
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "rows": rows},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    print("JSON:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
