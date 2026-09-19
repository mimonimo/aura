"""규정 검색 품질 측정 CLI — 본체는 zzaimy.eval.retrieval_eval (대시보드 카드의 유일한 수치 출처).

산출물: data/platform/eval/retrieval-latest.json (+ 날짜본), 사본 docs/retrieval-baseline-mini.md.
잠금: /tmp/zzaimy-heavy.lock (66_reindex.sh와 동일 — 무거운 작업 동시 1개). 코어 절반·nice 10.

실행(VM, 저장소 루트에서):
  정답 본문 채우기 — 조각 재분할 전에 1회. 이미 재분할됐으면 --snapshot으로 옛 조각 스냅샷을 준다:
    env PYTHONPATH=src .venv/bin/python scripts/53_eval_retrieval.py --backfill-gold
    env PYTHONPATH=src .venv/bin/python scripts/53_eval_retrieval.py --backfill-gold \\
        --snapshot data/platform/backup/regulation_chunks-<stamp>.json.gz
  측정 (대시보드 "재측정" 버튼과 같은 명령):
    env PYTHONPATH=src nohup .venv/bin/python scripts/53_eval_retrieval.py > /tmp/eval.log 2>&1 &
  옵션: --no-rerank (운영 구성 행 생략) · --limit N (질의 표본) · --rerank-sample N (기본 300)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.eval.retrieval_eval import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
