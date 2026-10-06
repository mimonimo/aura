"""선택한 DGX 원본 서식을 별도 작업 폴더에서 DOCX로 변환하고 구조 통계를 보존한다."""
import argparse
import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc",type=int,action="append",required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    from zzaimy.app.db import Database
    from zzaimy.app.archive_original import fetch
    from zzaimy.ingest import hwp5_docx,hwpx_docx
    args.out.mkdir(parents=True,exist_ok=False)
    db=Database(Path("data/platform/platform.db"))
    for ident in args.doc:
        doc=db.get_document(ident)
        if not doc:raise ValueError("document_missing")
        with db._conn() as conn:
            row=conn.execute("SELECT rel,size,mtime FROM archive_files WHERE doc_id=? AND removed_at=? LIMIT 1",(ident,"")).fetchone()
        if not row:raise ValueError("archive_missing")
        row=dict(row)
        suffix=Path(row["rel"]).suffix.lower()
        if suffix not in {".hwp",".hwpx"}:raise ValueError("native_hangul_required")
        work=args.out/str(ident);work.mkdir()
        temporary=fetch(row)
        source=work/("original"+suffix)
        try:shutil.copyfile(temporary,source)
        finally:temporary.unlink(missing_ok=True)
        raw=source.read_bytes()
        # 목차·조각 재조립 폴백을 사용하지 않는다. 직접 구조 변환 실패는 실패로 남긴다.
        converter=hwp5_docx if suffix==".hwp" else hwpx_docx
        data,stats=converter.convert(source,line_rule="atLeast")
        ns={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml=ET.fromstring(archive.read("word/document.xml"))
        tables=xml.findall(".//w:tbl",ns)
        (work/"working.docx").write_bytes(data)
        report={"doc_id":ident,"name":doc["filename"],"archive":row,
                "source_sha256":hashlib.sha256(raw).hexdigest(),"docx_sha256":hashlib.sha256(data).hexdigest(),
                "conversion":stats,"table_count":len(tables),
                "table_shapes":[{"rows":len(t.findall("w:tr",ns)),"grid_columns":len(t.findall("w:tblGrid/w:gridCol",ns))} for t in tables],
                "horizontal_merges":len(xml.findall(".//w:gridSpan",ns)),
                "vertical_merges":len(xml.findall(".//w:vMerge",ns)),
                "quality":"pending_visual_and_google_roundtrip"}
        (work/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
        (work/"text.txt").write_text("\n".join("".join(p.itertext()) for p in xml.findall(".//w:p",ns)))
        print(json.dumps(report,ensure_ascii=False,default=str),flush=True)


if __name__=="__main__":main()
