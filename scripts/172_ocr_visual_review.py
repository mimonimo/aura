#!/usr/bin/env python3
"""동일 PDF 페이지를 이미지로 판독한 결과를 원본과 나란히 보관한다.

내부 문서가 포함된 보고서는 외부 공개하지 않는다. 기존 171 측정/반입은 변경하지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.metadata
import importlib.util
import json
import platform
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def escape(value):
    return html.escape(str(value), quote=True)


def report_html(report):
    sections = []
    for page in report["pages"]:
        cards = []
        for result in page["results"]:
            status = "실패" if result["error"] else ("빈 결과" if not result["text"].strip() else "검수 대기")
            cards.append(f'<article><h3>{escape(result["engine"])} · {status}</h3>'
                         f'<p>{result["seconds"]:.2f}초 · {len(result["text"]):,}자</p>'
                         f'<p class="error">{escape(result["error"])}</p>'
                         f'<pre>{escape(result["text"])}</pre></article>')
        sections.append(f'<section><h2>원본 {page["number"]}쪽</h2><div class="comparison">'
                        f'<article><h3>원본 페이지</h3><img alt="원본 {page["number"]}쪽" '
                        f'src="data:image/png;base64,{page["image"]}"></article>'
                        + "".join(cards) + '</div><h3>원본 글자층 참고 · 정답 확정 아님</h3>'
                        f'<pre>{escape(page["reference_text"])}</pre></section>')
    return '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>OCR 시각 비교</title><style>
body{font:15px/1.6 system-ui,sans-serif;color:#243247;background:#f4f6f9;margin:0;padding:24px}
main{max-width:1800px;margin:auto}h1{font-size:24px}h2{font-size:20px}h3{font-size:16px}
section{margin:24px 0}.comparison{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;align-items:start}
article{background:white;border:1px solid #dce2ea;border-radius:10px;padding:16px;min-width:0}
img{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui;margin:0}
.error{color:#a62929}p{overflow-wrap:anywhere}header{background:white;padding:20px;border-radius:10px}
@media(max-width:480px){body{padding:12px}.comparison{grid-template-columns:1fr}}
@media print{body{background:white;padding:0}section{break-before:page}article{break-inside:avoid}}
</style><main><header><h1>OCR 페이지 시각 비교</h1>
<p>내부 검수용 · 원본과 추출 내용을 대조하세요. 자동 채택·학습 승인 자료가 아닙니다.</p>
<p>모든 엔진에 동일한 페이지 이미지를 입력합니다. 디지털 PDF 직접 추출 성능과는 별도 시험입니다.
표는 기존 비교 어댑터의 셀 텍스트로 표시되며, 병합·좌표·읽기 순서 정확도를 점수화하지 않습니다.</p>
''' + f'<p>문서: {escape(report["filename"])}<br>SHA-256: {escape(report["source_sha256"])}</p>' \
        + f'<pre>{escape(json.dumps(report["metadata"], ensure_ascii=False, indent=2))}</pre></header>' \
        + "".join(sections) + '</main></html>'


def adapter():
    spec = importlib.util.spec_from_file_location("ocr_duel", ROOT / "scripts/171_ocr_duel_grants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect(pdf, pages, engines, settings, duel=None):
    duel = duel or adapter()
    versions = {}
    for package in ("mineru", "docling", "pypdfium2"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "설치 정보 없음"
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    with pdf.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    report = dict(filename=pdf.name, source_sha256=digest, metadata={
        "created_at": datetime.now(timezone.utc).isoformat(), "git_revision": revision.stdout.strip(),
        "python": platform.python_version(), "packages": versions, "engines": engines,
        "settings_note": settings, "render_scale": 2.0, "input": "single-page raster PDF / image",
        "review_status": "pending", "scope": "page transcription; not full-document benchmark",
    }, pages=[])
    for number in pages:
        with tempfile.TemporaryDirectory(prefix="zz-ocr-review-") as tmp:
            work = Path(tmp)
            images, reference = duel.render(pdf, [number - 1], work)
            scan = work / "scan.pdf"
            duel.cer68.images_to_pdf(images, scan)
            page = dict(number=number, image=base64.b64encode(images[0].read_bytes()).decode("ascii"),
                        reference_text=reference, results=[])
            for engine in engines:
                started = time.monotonic()
                try:
                    text = duel.run_engine(engine, images, scan, work)
                    error = ""
                except Exception as exc:
                    text, error = "", f"{type(exc).__name__}: {exc}"
                page["results"].append(dict(engine=engine, text=text, error=error,
                                            seconds=round(time.monotonic() - started, 3)))
            report["pages"].append(page)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", required=True, help="원본 쪽 번호, 1부터. 예: 2,7,15")
    parser.add_argument("--engines", default="mineru,docling")
    parser.add_argument("--settings-note", required=True, help="모델/백엔드/언어/장비 등 실제 실행 설정. 비밀값 금지")
    parser.add_argument("--out", type=Path, required=True, help="새 결과 디렉터리; 기존 결과 덮어쓰기 금지")
    args = parser.parse_args()
    try:
        pages = list(dict.fromkeys(int(p.strip()) for p in args.pages.split(",")))
    except ValueError:
        parser.error("쪽 번호는 양의 정수 목록입니다")
    engines = list(dict.fromkeys(e.strip() for e in args.engines.split(",")))
    if not pages or min(pages) < 1 or not set(engines) <= {"mineru", "docling", "vision"}:
        parser.error("쪽 번호 또는 엔진이 올바르지 않습니다")
    args.out.mkdir(parents=True, exist_ok=False)
    result = collect(args.pdf, pages, engines, args.settings_note)
    (args.out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "report.html").write_text(report_html(result), encoding="utf-8")
    print(args.out / "report.html")
    return int(any(r["error"] or not r["text"].strip() for p in result["pages"] for r in p["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
