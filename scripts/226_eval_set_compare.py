"""문서 작성 시험 비교 — 결과 작업본(hwpx)과 정답(hwpx·pdf)을 절 제목으로 맞춰 절마다 견주고 markdown 리포트를 쓴다.

  python scripts/226_eval_set_compare.py data/eval_sets/<이름>                # 가장 최근 runs/<시각>/result.hwpx 와 answer
  python scripts/226_eval_set_compare.py data/eval_sets/<이름> --run <runs/시각 폴더>
  python scripts/226_eval_set_compare.py --answer 정답.hwpx --result 결과.hwpx [--evidence 공고.hwpx ...]
결과: <run>/report.md, <run>/score.json. 맨 끝 한 줄 EVAL_SET_SCORE {...} 은 기록용 요약.
숫자는 모두 기계 산출이다(절 채움·정답 수치 재현·근거 밖 수치·「확인 필요」 수·문자 3글자 묶음 재현율·표 칸 채움). 모델 채점은 하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.eval.eval_set import EvalSet, report_md, score  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set_dir", type=Path, nargs="?")
    ap.add_argument("--run", type=Path, help="runs/<시각> 폴더(기본: 가장 최근)")
    ap.add_argument("--answer", type=Path)
    ap.add_argument("--result", type=Path)
    ap.add_argument("--evidence", type=Path, nargs="*", default=None)
    ap.add_argument("--out", type=Path, help="리포트 폴더(기본: run 폴더, 없으면 결과 파일 옆)")
    a = ap.parse_args(argv)

    run_meta, name = None, ""
    if a.set_dir:
        es = EvalSet(a.set_dir)
        name = a.set_dir.name
        run_dir = a.run or max((p for p in (a.set_dir / "runs").iterdir() if p.is_dir()), default=None, key=lambda p: p.name) \
            if (a.set_dir / "runs").is_dir() else a.run
        answer = a.answer or es.answer
        result = a.result or (run_dir / "result.hwpx" if run_dir else None)
        evidence = a.evidence if a.evidence is not None else es.evidence
        if run_dir and (run_dir / "run.json").exists():
            run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        out = a.out or run_dir
    else:
        answer, result, evidence, out = a.answer, a.result, a.evidence or [], a.out
    if not answer or not Path(answer).exists():
        raise SystemExit("정답 파일(answer.hwpx 또는 answer.pdf)이 없습니다")
    if not result or not Path(result).exists():
        raise SystemExit("결과 파일(result.hwpx)이 없습니다 — 225_eval_set_run.py 를 먼저 돌리세요")
    out = Path(out or Path(result).parent)
    out.mkdir(parents=True, exist_ok=True)
    res = score(Path(answer), Path(result), [Path(p) for p in evidence])
    (out / "score.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report_md(res, name, run_meta), encoding="utf-8")
    print(report_md(res, name, run_meta))
    print("EVAL_SET_SCORE " + json.dumps(res["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
