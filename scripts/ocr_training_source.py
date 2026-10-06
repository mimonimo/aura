"""지정 PDF의 OCR 산출물과 페이지별 대조 자료를 보존한다. 학습 등록은 별도 검수 후 수행."""
import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault("HF_HUB_OFFLINE","1")
    spec=importlib.util.spec_from_file_location("parse_guard",ROOT/"scripts/167_dgx_parse.py")
    guard=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard._wait_for_memory(0)
    guard._limit_mineru()
    from zzaimy.ingest.parsers.mineru import MineruParser
    result=MineruParser(method="ocr",timeout_s=1800).parse(args.pdf,args.out/"mineru")
    report={"source_sha256":hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
            "quality":"pending_visual_review","result":dataclasses.asdict(result)}
    (args.out/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    import pypdfium2 as pdfium
    pdf=pdfium.PdfDocument(str(args.pdf))
    for i in range(len(pdf)):
        page=pdf[i]
        bitmap=page.render(scale=1.5)
        bitmap.to_pil().save(args.out/f"page-{i+1:02}.png")
        bitmap.close();page.close()
    pdf.close()
    print(json.dumps({"pages":len(result.pages),"tables":len(result.tables),
                      "quality":"pending_visual_review","out":str(args.out)}),flush=True)


if __name__=="__main__":
    main()
