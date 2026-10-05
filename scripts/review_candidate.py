"""Record source-checked agent findings without granting training approval."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zzaimy.dataset.candidate_review import save_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('findings', type=Path)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--output', type=Path, default=Path('data/training/candidate-reviews'))
    args = parser.parse_args()
    print(save_review(args.output, json.loads(args.candidate.read_text()),
                      json.loads(args.findings.read_text()), reviewer=args.reviewer,
                      reviewed_at=datetime.now(timezone.utc).isoformat()))


if __name__ == '__main__':
    main()
