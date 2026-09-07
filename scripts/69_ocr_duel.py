"""OCR 대결 벤치 — 현행 인식기 vs 도전자 VLM, 같은 잣대(어절 F1)로.

68번과 동일한 방식: 디지털 PDF 원문을 정답으로, 페이지를 이미지로 렌더해
도전자 OCR 엔드포인트(OpenAI 호환, 예: vLLM의 PaddleOCR-VL)에 통과시키고
순서 무관 어절 F1로 채점한다. 사람 채점도 단어 목록도 없다(하드코딩 금지).

현행 기준선은 docs/ocr-cer-bench.md의 같은 문서·쪽수 행과 비교한다.

무겁지 않게: 문서 1건·앞 N쪽, 페이지당 요청 1개 순차. flock으로 실행.
실행: PYTHONPATH=src .venv/bin/python scripts/69_ocr_duel.py <doc_id> [pages] [base_url]
  base_url 기본값 http://127.0.0.1:8002/v1 (도전자 서빙 포트)
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sqlite3
import sys
import urllib.request
from datetime import date
from pathlib import Path

DB = Path("data/platform/platform.db")
REPORT = Path("docs/ocr-duel.md")

# 68번 벤치의 채점·렌더 함수를 그대로 재사용 — 잣대가 갈라지면 대결이 무효다.
_spec = importlib.util.spec_from_file_location(
    "cer_bench", Path(__file__).parent / "68_ocr_cer_bench.py"
)
_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bench)


def _api(base_url: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload else "GET",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def ocr_page(base_url: str, model: str, png: Path) -> str:
    b64 = base64.b64encode(png.read_bytes()).decode()
    out = _api(base_url, "/chat/completions", {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "OCR:"},
            ],
        }],
    })
    return out["choices"][0]["message"]["content"] or ""


def main() -> None:
    doc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 172
    n_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    base_url = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8002/v1"

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT filename, stored_path FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    conn.close()
    if row is None:
        raise SystemExit(f"문서 {doc_id} 없음")

    model = _api(base_url, "/models")["data"][0]["id"]

    import tempfile

    with tempfile.TemporaryDirectory(prefix="zz-duel-") as tmp:
        tmp_p = Path(tmp)
        pages = _bench.render_pages(Path(row["stored_path"]), n_pages, tmp_p)
        texts = []
        for i, png in enumerate(pages, 1):
            t = ocr_page(base_url, model, png)
            texts.append(t)
            print(f"  {i}/{len(pages)}쪽: {len(t)}자")
        hyp = "\n\n".join(texts)
        ref = "".join(
            _bench.page_ground_truth(Path(row["stored_path"]), i)
            for i in range(len(pages))
        )
        f1 = _bench.word_f1(ref, hyp)
        rate = _bench.cer(ref, hyp)

    line = (
        f"| {date.today().isoformat()} | {row['filename'][:28]} (#{doc_id})"
        f" | {len(pages)}쪽 | {model} | {f1:.4f} | {rate:.4f} |"
    )
    if REPORT.exists():
        content = REPORT.read_text(encoding="utf-8").rstrip() + "\n" + line + "\n"
    else:
        content = "\n".join([
            "# OCR 대결 벤치 (도전자 모델)",
            "",
            "68번(ocr-cer-bench)과 같은 문서·같은 렌더·같은 채점(어절 F1,",
            "순서 무관)으로 도전자 OCR을 측정한다. 현행 기준선은",
            "docs/ocr-cer-bench.md의 동일 문서 행과 비교.",
            "",
            "| 측정일 | 문서 | 쪽수 | 도전자 | 어절F1 | CER(참고) |",
            "|---|---|---|---|---|---|",
            line,
        ]) + "\n"
    REPORT.write_text(content, encoding="utf-8")
    print(f"어절F1={f1:.4f} CER={rate:.4f} 도전자={model}")
    print("DUEL_DONE")


if __name__ == "__main__":
    main()
