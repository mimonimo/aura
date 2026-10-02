#!/usr/bin/env python3
"""사업 문서 OCR 대결 — 스캔 PDF 판독 도구(MinerU·docling·Writer 27B 비전)를 같은 쪽·같은 잣대로 견준다.

68·69 와 같은 원리: 글자층이 있는 디지털 PDF 의 쪽을 그림으로만 렌더해(글자층 없음 = 스캔본과 같은 조건) 판독하고,
원문 글자층을 정답으로 어절 F1(순서 무관)·CER(참고)을 잰다. 사람 채점·단어 목록 없음(절대 규칙 10).
표본은 원본 목록(scripts/165 JSONL)에서 사업별로 고르게 — 계획서·보고서·평가·기본계획 갈래의 PDF, 문서마다 고루 뽑은 쪽.

엔진은 실행하는 곳에 있는 것만 쓴다: DGX(GPU) = mineru·docling, VM = vision(토르의 Writer, 문서함과 같은 판독 경로).
같은 --sample 파일을 두 곳에서 쓰면 같은 쪽을 읽는다. 결과는 --out JSONL(문서·쪽·엔진·점수·시간 — 본문은 남기지 않는다).

사용(DGX): PYTHONPATH=src .venv-parse/bin/python scripts/171_ocr_duel_grants.py --inventory ~/archive_inventory.jsonl \\
             --root ~/data --make-sample ~/ocr_duel/sample.json --docs 24 --pages 3 --engines mineru,docling --out ~/ocr_duel/dgx.jsonl
      (VM):  … 171 --root <원본 캐시> --sample sample.json --engines vision --out data/eval/ocr_duel_vision.jsonl
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("cer68", ROOT / "scripts" / "68_ocr_cer_bench.py")
cer68 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cer68)

KINDS = {"plan", "report", "evaluation", "basic_plan"}
MIN_CHARS = 300          # 정답 쪽 글자 수 하한 — 표지·간지는 빼고 본문 쪽만


def _spread(n: int, k: int) -> list[int]:
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)}) if k > 1 else [n // 2]


def make_sample(inventory: Path, root: Path, n_docs: int, n_pages: int, seed: int) -> list[dict]:
    import pypdfium2 as pdfium

    by_prog: dict[str, list[dict]] = defaultdict(list)
    for line in inventory.read_text(encoding="utf-8").splitlines():
        try:
            it = json.loads(line)
        except ValueError:
            continue
        if it.get("ext") == "pdf" and it.get("kind") in KINDS and not it.get("dup_of") and it.get("program") \
                and 50_000 < int(it.get("size") or 0) < 60_000_000:
            by_prog[it["program"]].append(it)
    rng = random.Random(seed)
    for v in by_prog.values():
        rng.shuffle(v)
    out: list[dict] = []
    progs = sorted(by_prog, key=lambda p: -len(by_prog[p]))
    while len(out) < n_docs and any(by_prog[p] for p in progs):
        for p in progs:                                  # 사업마다 돌아가며 하나씩 — 큰 사업이 표본을 독차지하지 않게
            while by_prog[p] and len(out) < n_docs:
                it = by_prog[p].pop()
                try:
                    doc = pdfium.PdfDocument(str(root / it["rel"]))
                except Exception:
                    continue
                try:
                    good = []
                    for i in range(len(doc)):
                        tp = doc[i].get_textpage()
                        t = tp.get_text_bounded() or ""
                        tp.close()
                        if len("".join(t.split())) >= MIN_CHARS:
                            good.append(i)
                finally:
                    doc.close()
                if len(good) < n_pages:
                    continue                             # 글자층 없는(스캔) PDF 는 정답이 없다
                pages = [good[j] for j in _spread(len(good), n_pages)]
                out.append({"rel": it["rel"], "program": it["program"], "program_name": it.get("program_name"),
                            "kind": it.get("kind"), "pages": pages})
                break
    return out


def render(pdf: Path, pages: list[int], out_dir: Path) -> tuple[list[Path], str]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    imgs, ref = [], []
    try:
        for i in pages:
            p = out_dir / f"p{i + 1}.png"
            doc[i].render(scale=2.0).to_pil().save(p)
            imgs.append(p)
            tp = doc[i].get_textpage()
            ref.append(tp.get_text_bounded() or "")
            tp.close()
    finally:
        doc.close()
    return imgs, "\n".join(ref)


def _result_text(res) -> str:
    parts = [p.text for p in res.pages]
    for t in res.tables:
        parts.append("\n".join(c.text for c in t.cells))
    return "\n".join(parts)


def run_engine(name: str, imgs: list[Path], scan_pdf: Path, work: Path) -> str:
    if name == "mineru":
        from zzaimy.ingest.parsers.mineru import MineruParser
        return _result_text(MineruParser(method="ocr", timeout_s=900).parse(scan_pdf, work_dir=work / "mineru"))
    if name == "docling":
        from zzaimy.ingest.parsers.docling import DoclingParser
        return _result_text(DoclingParser().parse(scan_pdf))
    if name == "vision":
        from zzaimy.app.pipeline import DocumentProcessor
        proc = DocumentProcessor()
        proc.VISION_BUDGET_S = 10_000
        mds, _read = proc._vlm_pages([(i + 1, p) for i, p in enumerate(imgs)])
        return "\n\n".join(proc._md_to_text(m) for m in mds)
    raise SystemExit(f"모르는 엔진: {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="")
    ap.add_argument("--root", required=True)
    ap.add_argument("--make-sample", default="", help="표본을 새로 골라 이 파일에 적는다")
    ap.add_argument("--sample", default="", help="이미 고른 표본 파일")
    ap.add_argument("--docs", type=int, default=24)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--seed", type=int, default=171)
    ap.add_argument("--engines", default="mineru,docling")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    if args.make_sample:
        sample = make_sample(Path(args.inventory).expanduser(), root, args.docs, args.pages, args.seed)
        Path(args.make_sample).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(args.make_sample).expanduser().write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"표본 {len(sample)}건(사업 {len({s['program'] for s in sample})}개)", flush=True)
    else:
        sample = json.loads(Path(args.sample).expanduser().read_text(encoding="utf-8"))
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.is_file():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["rel"], r["engine"]))
    with out_path.open("a", encoding="utf-8") as fh:
        for s in sample:
            pdf = root / s["rel"]
            if not pdf.is_file():
                print("없음:", s["rel"][-60:], flush=True)
                continue
            with tempfile.TemporaryDirectory(prefix="zz-duel-") as tmp:
                work = Path(tmp)
                imgs, ref = render(pdf, s["pages"], work)
                scan_pdf = work / "scan.pdf"
                cer68.images_to_pdf(imgs, scan_pdf)
                for eng in engines:
                    if (s["rel"], eng) in done:
                        continue
                    t0 = time.time()
                    try:
                        hyp = run_engine(eng, imgs, scan_pdf, work)
                        err = ""
                    except Exception as e:  # 한 엔진 실패가 대결을 멈추지 않게 — 실패도 결과다
                        hyp, err = "", f"{type(e).__name__}: {e}"[:200]
                    rec = {"rel": s["rel"], "program": s["program"], "kind": s["kind"], "pages": len(s["pages"]), "engine": eng,
                           "f1": round(cer68.word_f1(ref, hyp), 4), "cer": round(cer68.cer(ref[:6000], hyp[:6000]), 4),
                           "ref_chars": len(ref), "hyp_chars": len(hyp), "sec": round(time.time() - t0, 1), "error": err}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"{eng:8} F1 {rec['f1']:.3f} CER {rec['cer']:.3f} {rec['sec']}초 {s['rel'][-50:]}", flush=True)
    # 요약
    rows = [json.loads(l) for l in out_path.read_text(encoding="utf-8").splitlines()]
    by = defaultdict(list)
    for r in rows:
        by[r["engine"]].append(r)
    for eng, rs in by.items():
        ok = [r for r in rs if not r["error"]]
        f1 = sorted(r["f1"] for r in ok)
        print(f"== {eng}: 문서 {len(rs)} · 실패 {len(rs) - len(ok)} · 어절F1 평균 {sum(f1) / max(len(f1), 1):.4f}"
              f" 중앙 {f1[len(f1) // 2] if f1 else 0:.4f} · 쪽당 {sum(r['sec'] for r in ok) / max(sum(r['pages'] for r in ok), 1):.1f}초", flush=True)
    print("OCR_DUEL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
