"""Validate local selection candidates; optionally export independently reviewed gold.

Input is a JSONL file, one retrieval case per line. No network or source writes.
Output creation is exclusive: an existing evaluation file is never overwritten.
"""
import argparse
import json
from pathlib import Path

from zzaimy.dataset.retrieval_cases import validate_case, evaluation_row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--export-gold', type=Path)
    args = parser.parse_args(argv)
    rows, problems, seen = [], [], set()
    for number, line in enumerate(args.input.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = validate_case(json.loads(line))
            if case['id'] in seen:
                raise ValueError('duplicate_case_id')
            seen.add(case['id'])
            rows.append(evaluation_row(case) if args.export_gold else case)
        except (ValueError, TypeError) as error:
            # Validation codes only; never log document excerpts.
            code = 'invalid_json' if isinstance(error, json.JSONDecodeError) else str(error)
            problems.append({'line': number, 'issue': code})
    if not rows and not problems:
        problems.append({'line': 0, 'issue': 'empty_cases'})
    if not problems and args.export_gold:
        with args.export_gold.open('x', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False)+'\n')
    print(json.dumps({'checked':len(rows)+len(problems), 'held':len(problems),
                      'exported':len(rows) if args.export_gold and not problems else 0,
                      'issues':problems}, ensure_ascii=False))
    return 2 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
