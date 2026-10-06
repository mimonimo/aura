"""명시적으로 지정한 로컬 규정/발전계획 5개를 읽어 학습 원문 명세를 만든다."""
import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zzaimy.dataset.tracks import key

NAMES = (
    "2026.08.19_규정집01.hwp", "2026.08.19_규정집02.hwp", "2026.08.27_지침집_2026.hwp",
    "영남이공대학교_중장기_연구보고서_최종_251016.pdf", "VISION2030.pdf",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    available = {unicodedata.normalize("NFC", p.name): p for p in args.directory.iterdir() if p.is_file()}
    if any(name not in available for name in NAMES):
        raise ValueError("지정한 5개 파일이 모두 있어야 합니다.")
    root = Path("data/training/tracks/institutional_policy")
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"track":"institutional_policy","authorization":"user-designated-five-files",
                "sources":[],"pending":[],"approved":False}
    for name in NAMES:
        path = available[name]
        raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix.lower() == ".hwp":
            from zzaimy.ingest.hwp_text import extract_hwp
            text = extract_hwp(path)
        else:
            from pypdf import PdfReader
            text = "\n\n".join(f"[페이지 {i+1}]\n" + (p.extract_text() or "") for i,p in enumerate(PdfReader(path).pages))
        if len(text.strip()) < 500:
            manifest["pending"].append({"title":name,"raw_sha256":raw_hash,"reason":"OCR 필요",
                                        "original_path":str(path)})
            print(json.dumps({"file":name,"status":"OCR 필요"},ensure_ascii=False),flush=True)
            continue
        output = root / (raw_hash + ".txt")
        if output.exists() and output.read_text() != text:
            raise ValueError("동일 원본 추출 결과 변경: 별도 버전 검토 필요")
        output.write_text(text)
        output.chmod(0o600)
        manifest["sources"].append({"id":raw_hash, "title":name, "raw_sha256":raw_hash,
                                   "text_path":str(output),"text_sha256":key(text)})
        print(json.dumps({"file":name, "characters":len(text)},ensure_ascii=False),flush=True)
    dest = root / "manifest.json"
    dest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    dest.chmod(0o600)


if __name__ == "__main__":
    main()
