#!/usr/bin/env python3
"""ZZAIMY-Writer(27B) QLoRA 지도학습 준비·실행.

브리프 절대규칙 2를 코드로 지킨다 — 베이스라인 측정 기록이 없으면 학습을 시작하지
않는다. 순서를 바꾸면 축 B 의 성과를 입증할 수 없기 때문이다.

쓰는 곳: 학습 담당 장비(DGX). 서빙 장비(젯슨 토르)와 역할이 다르다.
학습 결과는 LoRA 어댑터만 토르로 옮기고(scripts/94_ship_adapter.sh), 토르가 베이스 위에
얹어 서빙한다(scripts/82_serve_writer.sh). 27B 병합본을 옮기지 않는다.

사용:
  python scripts/83_sft_writer_qlora.py --check          # 준비 상태만 점검
  python scripts/83_sft_writer_qlora.py --base <경로> --data <jsonl> --out <경로>
  python scripts/83_sft_writer_qlora.py --base <경로> --smoke   # 합성 4쌍으로 2스텝 — 장비에서 27B LoRA 가 도는지만 본다

정밀도: 기본은 bf16 LoRA 다(DGX 통합 메모리 121GB 에 27B bf16 54GB 가 들어가고, 학습본을 bf16 으로 병합한 뒤
NVFP4 로 양자화해 서빙하는 경로가 ADR-0023). --4bit 를 주면 bitsandbytes QLoRA — aarch64 CUDA 13 에 bnb 휠이
있을 때만 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "data" / "train" / "baselines" / "writer" / "baseline.json"


def read_baseline() -> dict | None:
    """베이스라인 측정 기록 — 없으면 None. 이 기록이 학습의 전제다."""
    if not BASELINE.exists():
        return None
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def count_pairs(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def check(data: Path | None) -> int:
    print("학습 준비 상태")
    base = read_baseline()
    if base is None:
        print(f"  베이스라인 측정 기록: 없음 ({BASELINE.relative_to(ROOT)})")
        print("  베이스라인을 먼저 측정해야 학습을 시작할 수 있습니다.")
    else:
        print(f"  베이스라인 측정 기록: 있음 · {base.get('measured_at', '시각 미기재')}")
    if data is not None:
        n = count_pairs(data)
        print(f"  학습 예시: {n}건 ({data})")
    try:
        import torch
        print(f"  GPU: {torch.cuda.device_count()}대 · {torch.__version__}")
    except ImportError:
        print("  GPU: 확인 불가 (이 장비에 torch 가 없습니다)")
    return 0 if base is not None else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="준비 상태만 점검하고 끝낸다")
    ap.add_argument("--base", default="", help="베이스 모델 경로 또는 허브 이름 (27B)")
    ap.add_argument("--data", default=str(ROOT / "data" / "train" / "sft.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data" / "train" / "models" / "writer" / "v1-adapter"))
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--seq-len", type=int, default=4096)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--accum", type=int, default=16)
    ap.add_argument("--4bit", dest="four_bit", action="store_true", help="bitsandbytes QLoRA(기본은 bf16 LoRA)")
    ap.add_argument("--smoke", action="store_true", help="합성 4쌍 2스텝 — 베이스라인 없이도 장비 검증용으로만")
    args = ap.parse_args()

    data = Path(args.data)
    if args.check:
        return check(data)
    if args.smoke:
        # 합성 4쌍(configs/training/data/smoke_pairs.json)을 대화 형식 jsonl 로 임시 생성 — 실제 문서는 쓰지 않는다
        import json as _json

        pairs = _json.loads((ROOT / "configs" / "training" / "data" / "smoke_pairs.json").read_text())
        data = Path("/tmp/zzaimy-writer-smoke.jsonl")
        data.write_text("\n".join(_json.dumps({"messages": [
            {"role": "user", "content": (ex.get("instruction", "") + "\n" + ex.get("input", "")).strip()},
            {"role": "assistant", "content": ex.get("output", "")}]}, ensure_ascii=False) for ex in pairs) + "\n")
        args.out, args.epochs, args.seq_len, args.accum = "/tmp/zzaimy-writer-smoke", 1.0, 512, 1

    if read_baseline() is None and not args.smoke:
        print("베이스라인 측정 기록이 없어 학습을 시작하지 않습니다.", file=sys.stderr)
        print(f"먼저 베이스라인을 측정해 {BASELINE.relative_to(ROOT)} 에 남기십시오.", file=sys.stderr)
        return 2
    if not args.base:
        print("--base 로 베이스 모델을 지정하십시오.", file=sys.stderr)
        return 2
    n = count_pairs(data)
    if n == 0:
        print(f"학습 예시가 없습니다 — {data}", file=sys.stderr)
        print("데이터 공방(/dev/data)에서 수치 검증을 통과한 예시를 먼저 만드십시오.", file=sys.stderr)
        return 2

    print(f"학습을 시작합니다 — 예시 {n}건 · 순위 {args.rank} · 길이 {args.seq_len}")
    try:
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import SFTConfig, SFTTrainer
        import torch
    except ImportError as e:
        print(f"학습 묶음이 이 장비에 없습니다 — {e}", file=sys.stderr)
        print("학습 전용 가상환경(.venv-train)에서 실행하십시오.", file=sys.stderr)
        return 3

    # 통합 메모리 장비(DGX·토르)에서는 전부 GPU 에 올린다. device_map="auto" 는 그 순간의 여유 메모리만 보고
    # 일부 층을 CPU/meta 로 내려 역전파가 "expected device meta but got cuda:0" 으로 죽는다(2026-09-22 실측).
    load_kw: dict = {"dtype": torch.bfloat16, "device_map": {"": 0} if torch.cuda.is_available() else None}
    if args.four_bit:
        load_kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
        )
    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(args.base, **load_kw)
    ds = load_dataset("json", data_files=str(data), split="train")
    trainer = SFTTrainer(
        model=model,
        processing_class=tok,
        train_dataset=ds,
        peft_config=LoraConfig(
            r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05,
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
        ),
        args=SFTConfig(
            output_dir=args.out, num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.accum,
            learning_rate=args.lr, bf16=True, logging_steps=1 if args.smoke else 10,
            save_strategy="no" if args.smoke else "epoch", max_length=args.seq_len,
            gradient_checkpointing=True, report_to=[],
            max_steps=2 if args.smoke else -1,
        ),
    )
    trainer.train()
    if args.smoke:
        peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0
        print(f"스모크 통과 — 2스텝, GPU 최대 사용 {peak:.1f}GB")
        return 0
    trainer.save_model(args.out)
    print(f"학습을 마쳤습니다 — {args.out}")
    print("다음: scripts/94_ship_adapter.sh 로 토르에 옮긴 뒤 scripts/82_serve_writer.sh 로 띄우십시오.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
