#!/usr/bin/env python3
"""리랭커 점수 하한 재측정 — 모델을 바꾸면 눈금이 달라지므로 하한도 다시 정한다.

RERANK_MIN(기본 0.1)은 "1위조차 이 값 아래면 근거가 약하다"는 절대 하한이다. 눈금은
모델·쌍 길이에 따라 움직이므로 파인튜닝본으로 갈아탈 때마다 다시 재야 한다. 재는 방법은
처음 정할 때와 같다(2026-09-20):

  · 정답이 있는 질의: 1위 점수의 하위 5%·중앙값 — 이 아래로 하한을 내리면 정상 질의가 약함으로 찍힌다.
  · 근거가 없어야 하는 질의('문서 접수 해줘'·'안녕하세요'): 1위 점수의 최댓값 — 이 위로 하한을 올려야
    무관 질의가 걸러진다.
  · 권하는 하한 = (무관 최댓값 + 정답 하위 5%) / 2. 두 분포가 겹치면 겹친다고 적고 하한을 권하지 않는다.

사용 (VM 에서, 리랭커가 켜진 상태):
  env PYTHONPATH=src ZZAIMY_RERANK_URL=http://…:8013/score .venv/bin/python scripts/105_rerank_floor.py
  옵션: --sample 60 --queries data/interim/synth_queries_paraphrase.jsonl
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.rerank import rerank_scored  # noqa: E402
from zzaimy.eval import retrieval_eval as rev  # noqa: E402

# 근거가 없어야 하는 질의 — 업무 지시·인사말. 규정 조각이 1위로 올라와도 근거는 아니다.
# 근거가 없어야 하는 질의 — 인사말·업무 지시. 규정 조각이 1위로 올라와도 근거는 아니다.
# 여기에 '학교 규정 좀 알려줘' 같은 질의를 섞으면 측정이 무너진다 — 그건 실제로 규정을
# 찾아야 하는 질의이고 점수가 높은 것이 정답이다(2026-09-20 실측에서 이 실수를 잡았다).
META_QUERIES = [
    "문서 접수 해줘", "안녕하세요", "오늘 일정 알려줘", "이 파일 업로드할게",
    "감사합니다", "지난번에 말한 그거 어떻게 됐어", "회의 잡아줘", "출력해서 가져다줘",
    "메일 보냈어요", "확인 부탁드립니다", "지금 몇 시야", "테스트",
    "잘 부탁드립니다", "수고하셨습니다", "파일 이름 바꿔줘",
    "결재 올렸습니다", "오늘 중으로 보내주세요", "요청하신 대로 수정했습니다",
    "내일 회의 자료 준비할게요", "파일 다시 올려 주세요",
]

# 애매한 질의 — 규정과 상관이 있을 수도 있다. 하한을 정하는 데 쓰지 않고, 정한 하한이
# 이들을 어떻게 판정하는지만 함께 적는다(참고용).
VAGUE_QUERIES = [
    "학교 규정 좀 알려줘", "보고서 초안 부탁해요", "사업 관련해서 물어볼 게 있어요",
    "담당자 연락처 알려줘", "서류 준비 다 됐나요", "언제까지 제출하면 되나요",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--queries", default="")
    ap.add_argument("--sample", type=int, default=60)
    args = ap.parse_args()

    db = Database(Path(args.db))
    chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}
    prod = rev.production_retrievers(db, chunks)
    rows = rev.load_rows(Path(args.queries) if args.queries else rev.QUERIES_PATH)
    golds, _ = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
    pairs = [(t, golds[i]) for i, r in enumerate(rows) if golds[i]
             for qt in rev.QUERY_TYPES if (t := (r.get(qt) or "").strip())]
    idx = sorted(random.Random(rev.SEED).sample(range(len(pairs)), min(args.sample, len(pairs))))

    def top_score(q: str) -> tuple[float, float] | None:
        """(1위 점수, 격차) — 격차는 1위와 나머지 후보 평균의 차이."""
        cand = prod.hybrid(q, prod.lexical(q), prod.dense(q))[: rev.TOP_K]
        got = rerank_scored(q, [by_id[c] for c in cand if c in by_id])
        if not got:
            return None
        rest = [s for _, s in got[1:]]
        return got[0][1], got[0][1] - (sum(rest) / len(rest) if rest else 0.0)

    good = [v for i in idx if (v := top_score(pairs[i][0])) is not None]
    meta = [v for q in META_QUERIES if (v := top_score(q)) is not None]
    vague = [(q, v) for q in VAGUE_QUERIES if (v := top_score(q)) is not None]
    good_gap = sorted(g for _, g in good)
    meta_gap = sorted(g for _, g in meta)
    good = [s for s, _ in good]
    meta = [s for s, _ in meta]
    if not good or not meta:
        print("점수를 얻지 못했습니다 — 리랭커가 켜져 있는지 확인하십시오.")
        return 1
    good.sort()
    p05 = good[max(0, int(len(good) * 0.05) - 1)]
    print(f"정답 질의 {len(good)}건 · 1위 점수 하위5% {p05:.3f} · 중앙 {statistics.median(good):.3f}"
          f" · 최솟값 {good[0]:.3f}")
    print(f"무관 질의 {len(meta)}건 · 1위 점수 최댓값 {max(meta):.3f} · 중앙 {statistics.median(meta):.3f}")
    g05 = good_gap[max(0, int(len(good_gap) * 0.05) - 1)]
    print(f"격차(1위 - 나머지 평균) · 정답 하위5% {g05:.3f} · 중앙 {statistics.median(good_gap):.3f}"
          f" · 최솟값 {good_gap[0]:.3f}")
    print(f"격차 · 무관 최댓값 {max(meta_gap):.3f} · 중앙 {statistics.median(meta_gap):.3f}")
    if max(meta) >= p05:
        print("절대 점수로는 두 분포가 겹칩니다 — 하한 하나로 가릴 수 없습니다.")
        if max(meta_gap) < g05:
            print(f"격차는 갈립니다 — 격차 하한 {(max(meta_gap) + g05) / 2:.3f} 로 판정하는 편이 맞습니다.")
        else:
            print("격차로도 갈리지 않습니다 — 지금 규칙을 그대로 두고 이유를 기록하십시오.")
        return 0
    # 후보 하한마다 양쪽 오판을 센다 — '정답을 약함으로 찍는 수'와 '무관을 통과시키는 수'.
    cands = sorted({round(max(meta) + 0.005, 3), round((max(meta) + p05) / 2, 3), round(p05, 3)})
    print("하한 후보별 오판:")
    for f in cands:
        false_weak = sum(1 for s_ in good if s_ < f)
        leaked = sum(1 for s_ in meta if s_ >= f)
        over = [q for q, (s_, _) in vague if s_ >= f]
        print(f"  {f:.3f} → 정답을 약함으로 {false_weak}/{len(good)}건"
              f" · 무관 통과 {leaked}/{len(meta)}건"
              f" · 애매한 질의 통과 {len(over)}/{len(vague)}건 {'(' + ', '.join(over) + ')' if over else ''}")
    print(f"권하는 하한(RERANK_MIN) {cands[0]:.3f} — 무관을 막는 가장 낮은 값(정답 오판을 최소로)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
