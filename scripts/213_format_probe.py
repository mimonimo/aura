#!/usr/bin/env python3
"""답 꼴 점검 — 학습셋의 틀(「근거 설명: … 답변: …」, 「○○ 원문: …」)이 플랫폼 꼴 요청에도 새어 나오는가.

학습셋 시험 문항은 학습 꼴 그대로라 틀이 나와도 정상이다. 플랫폼은 이 틀을 요청하지 않으므로 평범한 채팅 질문으로 따로 본다.
판 셋: 시스템 지시 없음 / 플랫폼 답변 시스템 지시(src/zzaimy/app/responder.py 의 _SYSTEM 을 글로 읽음) / 학습셋 시스템 지시 한 줄.
질문은 개인정보 없는 일반 업무 질문(PROMPTS). 같은 생성 설정(온도 0.2, 생각 끔)으로 비교 대상마다 부르고 코드가 센다(210 FRAME).

사용(VM): python3 213_format_probe.py --models "open-nvfp4:zzaimy-writer=http://<토르 03>/v1,sft-merged:writer-sft-merged=http://<토르 02>/v1" \\
    --responder ~/zzaimy-capstone/src/zzaimy/app/responder.py --out ~/ops/sft_eval/format-run
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("s210", HERE / "210_sft_heldout_compare.py")
s210 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s210)

TRAIN_SYSTEM = "문서 근거와 앞 대화를 확인해 답합니다. 접근 권한은 서버가 판단하며 우회하지 않습니다."
PROMPTS = [
    "교육혁신 사업의 목표를 한 문장으로 써 줘.",
    "산학협력단이 하는 일을 세 줄로 정리해 줘.",
    "학생 상담 프로그램 운영 계획의 목차를 잡아 줘.",
    "사업계획서에서 '추진 배경' 절은 보통 무엇을 쓰는지 알려 줘.",
    "다음 주 부서 회의 안건을 개조식으로 정리해 줘: 예산 집행 점검, 하반기 행사 일정, 시설 보수 요청.",
    "결과보고서와 실적보고서의 차이를 설명해 줘.",
    "현장실습 운영 시 확인해야 할 사항을 표로 정리해 줘.",
    "이 문장을 행정 문서 문체로 고쳐 줘: 우리는 학생들이 취업을 더 잘하게 도와주려고 합니다.",
    "AI 교육 사업의 기대 효과를 개조식 네 줄로 써 줘.",
    "공문에서 '붙임' 표기는 어떻게 쓰는지 알려 줘.",
    "지역 연계 봉사 프로그램 홍보 문구를 두 문장으로 만들어 줘.",
    "사업 성과지표를 정할 때 주의할 점을 알려 줘.",
    "교직원 연수 계획의 추진 일정 표 머리줄을 제안해 줘.",
    "회의록 양식에 꼭 들어가야 할 항목을 알려 줘.",
    "예산 계획에서 인건비와 운영비를 나눠 적는 까닭을 짧게 설명해 줘.",
]


def platform_system(path: Path) -> str:
    """responder.py 의 _SYSTEM 글(삼중 따옴표)만 읽는다 — 앱을 띄우지 않고 같은 지시를 쓴다."""
    m = re.search(r'_SYSTEM\s*=\s*"""(.*?)"""', path.read_text(encoding="utf-8"), re.S)
    return m.group(1) if m else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="답 꼴 점검(학습셋 틀이 평범한 요청에 새어 나오는가)")
    ap.add_argument("--models", required=True)
    ap.add_argument("--base-url", default="")
    ap.add_argument("--responder", default=str(HERE.parent / "src" / "zzaimy" / "app" / "responder.py"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    arms, urls, served = s210.parse_models(a.models, a.base_url)
    psys = platform_system(Path(a.responder).expanduser())
    if not psys:
        raise SystemExit("플랫폼 시스템 지시를 읽지 못함(--responder)")
    conds = {"지시 없음": None, "플랫폼 답변 지시": psys, "학습셋 지시": TRAIN_SYSTEM}
    cases = [{"id": f"fp-{ci}-{i:02d}", "cond": c, "messages": ([{"role": "system", "content": sysm}] if sysm else []) + [{"role": "user", "content": q}]}
             for ci, (c, sysm) in enumerate(conds.items()) for i, q in enumerate(PROMPTS)]
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    if not a.report_only:
        for arm in arms:
            path = out / f"gen-{arm}.jsonl"
            done = s210.read_cache(path)
            todo = [c for c in cases if c["id"] not in done]
            print(f"[format] {arm}: {len(done)} 있음, {len(todo)} 남음", flush=True)

            def one(c, arm=arm, path=path):
                g = s210.chat(urls[arm], served[arm], c["messages"], a.max_tokens, a.temperature, seed=a.seed)
                s210.append(path, {"key": c["id"], "arm": arm, "model": served[arm], **g}, lock)
                return g

            with ThreadPoolExecutor(max_workers=max(1, min(a.concurrency, 2))) as ex:
                list(ex.map(one, todo))
    rep = {"prompts": len(PROMPTS), "arms": {}}
    for arm in arms:
        gens = s210.read_cache(out / f"gen-{arm}.jsonl")
        per = {}
        for c in cases:
            if c["id"] in gens:
                per.setdefault(c["cond"], []).append(bool(s210.FRAME.search(gens[c["id"]]["text"])))
        rep["arms"][arm] = {k: {"n": len(v), "frame": sum(v), "rate": round(sum(v) / len(v), 3)} for k, v in per.items()}
        rep["arms"][arm]["예시"] = [c["id"] for c in cases if c["id"] in gens and s210.FRAME.search(gens[c["id"]]["text"])][:10]
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["| 비교 대상 | 판 | 틀이 나온 답 | 비율 |", "|---|---|---|---|"]
    for arm, d in rep["arms"].items():
        for k, x in d.items():
            if k != "예시":
                lines.append(f"| {arm} | {k} | {x['frame']}/{x['n']} | {x['rate']} |")
    print("\n".join(lines), flush=True)
    print("FORMAT_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
