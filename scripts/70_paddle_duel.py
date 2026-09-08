"""도전자 PaddleOCR-VL(0.9B) 대결 — 현행 MinerU와 같은 잣대(어절 F1)로. CPU.

68번의 렌더·채점 함수를 임포트해 같은 문서·같은 페이지·같은 어절 F1로
PaddleOCR-VL을 측정한다. GPU 없이 transformers로 직접 구동(float32).
실행: PYTHONPATH=src .venv-paddle/bin/python scripts/70_paddle_duel.py <doc_id> [pages]
"""
from __future__ import annotations
import importlib.util, sqlite3, sys, tempfile
from datetime import date
from pathlib import Path

DB = Path("data/platform/platform.db")
REPORT = Path("docs/ocr-duel.md")
_spec = importlib.util.spec_from_file_location("cer_bench", Path(__file__).parent / "68_ocr_cer_bench.py")
_bench = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_bench)


def _patch_causal_mask() -> None:
    """버전 호환 어댑터: 모델 코드는 create_causal_mask(inputs_embeds=)로 부르나
    transformers 4.55는 input_embeds=를 받는다. 이름만 다르므로 얇게 변환.
    (OCR 로직이 아니라 순수 API 호환 처리 — 인식 결과에 개입하지 않는다.)"""
    import inspect
    import transformers.masking_utils as mu
    orig = mu.create_causal_mask
    params = inspect.signature(orig).parameters
    if "input_embeds" in params and "inputs_embeds" not in params:
        def shim(*a, **k):
            if "inputs_embeds" in k:
                k["input_embeds"] = k.pop("inputs_embeds")
            return orig(*a, **k)
        mu.create_causal_mask = shim


def main() -> None:
    doc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    n_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT filename, stored_path FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if row is None: raise SystemExit(f"문서 {doc_id} 없음")

    import torch
    _patch_causal_mask()
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor
    MID = "PaddlePaddle/PaddleOCR-VL"
    print("모델 로드(CPU float32)...")
    model = AutoModelForCausalLM.from_pretrained(MID, trust_remote_code=True, torch_dtype=torch.float32).to("cpu").eval()
    proc = AutoProcessor.from_pretrained(MID, trust_remote_code=True)

    def ocr(img: Image.Image) -> str:
        msgs = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": "OCR:"}]}]
        inputs = proc.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                          return_dict=True, return_tensors="pt").to("cpu")
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=1024, do_sample=False)
        return proc.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]

    import time
    with tempfile.TemporaryDirectory(prefix="zz-paddle-") as tmp:
        pages = _bench.render_pages(Path(row["stored_path"]), n_pages, Path(tmp))
        texts = []
        for i, p in enumerate(pages, 1):
            t = time.time(); txt = ocr(Image.open(p).convert("RGB")); texts.append(txt)
            print(f"  {i}/{len(pages)}쪽: {len(txt)}자 ({round(time.time()-t,1)}초)")
        hyp = "\n\n".join(texts)
        ref = "".join(_bench.page_ground_truth(Path(row["stored_path"]), i) for i in range(len(pages)))
        f1 = _bench.word_f1(ref, hyp); cer = _bench.cer(ref, hyp)

    line = (f"| {date.today().isoformat()} | {row['filename'][:28]} (#{doc_id}) | {len(pages)}쪽 "
            f"| PaddleOCR-VL-0.9B (CPU) | {f1:.4f} | {cer:.4f} |")
    if REPORT.exists():
        content = REPORT.read_text(encoding="utf-8").rstrip() + "\n" + line + "\n"
    else:
        content = "\n".join(["# OCR 대결 벤치 (도전자 모델)", "",
            "68번(ocr-cer-bench)과 같은 문서·같은 렌더·같은 채점(어절 F1)으로 도전자를 측정.",
            "현행 MinerU 기준선은 docs/ocr-cer-bench.md의 동일 문서 행과 비교.", "",
            "| 측정일 | 문서 | 쪽수 | 도전자 | 어절F1 | CER(참고) |",
            "|---|---|---|---|---|---|", line]) + "\n"
    REPORT.write_text(content, encoding="utf-8")
    print(f"어절F1={f1:.4f} CER={cer:.4f} (PaddleOCR-VL, {row['filename'][:30]}, {len(pages)}쪽)")
    print("DUEL_DONE")


if __name__ == "__main__":
    main()
