"""Print candidate inventory, excluding older results for identical source windows."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zzaimy.dataset.candidate_status import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/training/generated-candidates'))
    parser.add_argument('--reviews', type=Path, default=Path('data/training/candidate-reviews'))
    args = parser.parse_args()
    if not args.input.is_dir():
        parser.error('candidate directory does not exist')
    invalid = []
    def records():
        for path in sorted(args.input.glob('*.json')):
            try:
                value = json.loads(path.read_text())
                context = value['context']
                if not isinstance(value['source_window'], list) or not isinstance(value['status'], str):
                    raise ValueError()
                context['program_id'], context['doc_id']
                yield value
            except (OSError, ValueError, KeyError, TypeError):
                invalid.append(path.name)
    reviews, invalid_reviews = [], []
    for path in sorted(args.reviews.glob('*.json')):
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or not isinstance(value.get('job'), str):
                raise ValueError('invalid_review')
            reviews.append(value)
        except (OSError, ValueError, TypeError):
            invalid_reviews.append(path.name)
    report = summarize(records(), reviews)
    report['invalid_review_files'] = invalid_reviews
    report['invalid_files'] = invalid
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
