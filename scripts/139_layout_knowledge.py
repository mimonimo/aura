#!/usr/bin/env python3
"""데이터 체계 이관(ADR-0031) — 지식(색인·측정·옛 파일럿)·캐시·학습 자료를 세 층 자리로 옮긴다.

  chunk_embeddings.* · doc_vectors.npz · question_embeddings.npz → knowledge/index/
  eval/ · data/eval/llm-rerank-*                                → knowledge/eval/
  corpus_pilot.db · corpus_pilot_embeddings.*                   → knowledge/corpus_pilot/
  lines/ · pagecache/ · restored/                               → cache/
  data/eval/baseline*.json                                       → data/train/baselines/writer/
DB 는 건드리지 않는다(경로가 DB 에 없다). 기본은 미리보기. 서비스가 색인을 읽는 중이면 재시작이 필요하다(99 --restart).

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/139_layout_knowledge.py [--data data/platform] [--train data/train] [--apply]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app import paths  # noqa: E402


def plan_moves(base: Path, train: Path, repo_eval: Path) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    for name in ("chunk_embeddings.npz", "chunk_embeddings.meta.json", "doc_vectors.npz", "question_embeddings.npz"):
        if (base / name).exists():
            moves.append((base / name, paths.index_dir(base) / name))
    for extra in base.glob("chunk_embeddings.*.npz"):        # 후보 색인(v1·fresh 등)
        moves.append((extra, paths.index_dir(base) / extra.name))
    if (base / "eval").is_dir():
        for f in sorted((base / "eval").iterdir()):
            moves.append((f, paths.eval_dir(base) / f.name))
    for name in ("corpus_pilot.db", "corpus_pilot_embeddings.npz", "corpus_pilot_embeddings.meta.json"):
        if (base / name).exists():
            moves.append((base / name, paths.corpus_dir(base) / name))
    for name in ("lines", "pagecache", "restored"):
        if (base / name).is_dir():
            moves.append((base / name, paths.cache_dir(base) / name))
    if repo_eval.is_dir():
        for f in sorted(repo_eval.iterdir()):
            if f.name.startswith("baseline"):
                moves.append((f, train / "baselines" / "writer" / f.name))
            else:
                moves.append((f, paths.eval_dir(base) / f.name))
    return moves


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/platform")
    ap.add_argument("--train", default="data/train")
    ap.add_argument("--repo-eval", default="data/eval")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    base, train = Path(args.data), Path(args.train)
    moves = plan_moves(base, train, Path(args.repo_eval))
    print(f"옮길 항목 {len(moves)}개")
    for s, d in moves[:12]:
        print(f"  {s} → {d}")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply")
        return 0
    paths.ensure_layout(base)
    for model in ("writer",):
        (train / "baselines" / model).mkdir(parents=True, exist_ok=True)
        (train / "datasets" / model).mkdir(parents=True, exist_ok=True)
        (train / "models" / model).mkdir(parents=True, exist_ok=True)
        (train / "runs" / model).mkdir(parents=True, exist_ok=True)
    done = 0
    for s, d in moves:
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            if d.is_dir() and s.is_dir():
                for f in s.iterdir():
                    shutil.move(str(f), str(d / f.name))
                shutil.rmtree(s, ignore_errors=True)
            else:
                print("  이미 있음, 건너뜀:", d)
                continue
        else:
            shutil.move(str(s), str(d))
        done += 1
    for leftover in (base / "eval", Path(args.repo_eval)):
        if leftover.is_dir() and not any(leftover.iterdir()):
            leftover.rmdir()
    print(f"적용 {done}개. 서비스가 색인을 다시 읽도록 재시작하십시오(scripts/99_deploy.sh --restart).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
