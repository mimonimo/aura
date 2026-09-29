#!/usr/bin/env python3
"""읽기 전용: python scripts/audit_sft_quality.py data/training/tree_cot_pairs.jsonl.

보류가 있으면 exit 2. 원문·개인정보는 출력하지 않는다. 생성/라벨링과 별개로 실행.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zzaimy.dataset.quality_gate import audit_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    pairs = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    result = audit_dataset(pairs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result['held'] or not result['total'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
