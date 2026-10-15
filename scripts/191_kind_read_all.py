#!/usr/bin/env python3
"""서류 갈래 27B 일괄 판정 — DGX 처리 결과(scripts/167 parsed-*.jsonl)의 문서마다 app/kind_read 로 갈래를 정해 ~/parsed/kind27b-0.jsonl 에 덧붙인다.

판 바뀜(version)까지 같은 문서는 다시 판정하지 않는다(이어 돌기). 규칙이 비워 둔 문서부터 한다.
VM 이 5분 주기로 이 파일을 받아(170 --parsed) scripts/192 로 문서 갈래에 반영한다. 판정 실패·「모름」 은 적지 않는다(규칙 갈래 유지).
모델은 DGX 의 Ollama(--url·--model, 기본은 Writer 와 같은 계열의 27B). --max-sec 로 한 번에 돌 시간을 묶어 30분 주기(zz_dgx_cycle)에서 새 문서만 이어 돈다.
판정 줄마다 판정한 모델 정보(llm — 서빙 이름·digest 또는 바탕 가중치, generate.model_info)를 남긴다. 예전 줄(llm 없음)은 「모름」.
--redo-model 이면 지금 모델과 다른(또는 모르는) 모델이 내린 판정을 앞에서 다시 본다 — 모델을 바꾼 뒤 재판정할 때.
사용(DGX): PYTHONPATH=src .venv-parse/bin/python scripts/191_kind_read_all.py [--max-sec 900] [--redo evidence] [--redo-model]
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from zzaimy.app import kind_read  # noqa: E402

_spec = importlib.util.spec_from_file_location("s190", ROOT / "scripts" / "190_kind_read_sample.py")
s190 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s190)


def redo_by_model(last: dict[str, dict], docs: dict[str, dict], todo: list[str], llm: dict) -> list[str]:
    """지금 모델과 다른(모르는) 모델이 내린 판정 — 아직 남은 일 앞에 둔다. 같은 문서를 두 번 넣지 않는다."""
    from zzaimy.generate import model_info
    pending = set(todo)
    redo = sorted(r for r, j in last.items() if r in docs and r not in pending and not model_info.same(model_info.of(j), llm))
    print(f"다른 모델이 내린 판정 다시 보기 {len(redo)}", flush=True)
    return redo + todo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default=str(Path.home() / "parsed"))
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen3.8:27b")
    ap.add_argument("--redo", default="", help="이 갈래(쉼표)로 판정된 문서 가운데 지금 지시(kind_read.PROMPT_VER)가 아닌 판정을 먼저 다시 본다")
    ap.add_argument("--redo-model", action="store_true",
                    help="지금 판정 모델과 다른 모델(또는 모델 정보가 없는 예전 줄)이 내린 판정을 먼저 다시 본다")
    ap.add_argument("--role", default="", help="이 용도의 연결(VllmClient)로 판정한다 — 기본은 --url 의 Ollama")
    ap.add_argument("--max-sec", type=int, default=0, help="이 시간(초)이 지나면 멈춘다(0 = 끝까지)")
    args = ap.parse_args()
    out = Path(args.parsed) / "kind27b-0.jsonl"
    lock = open(Path(args.parsed) / "kind27b.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("다른 판정이 돌고 있다 — 이번은 건너뜀", flush=True)
        return 0
    done: dict[str, str] = {}
    last: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                done[d["rel"]] = str(d.get("version") or "")
                last[d["rel"]] = d
            except (ValueError, KeyError):
                continue
    docs = s190.latest(args.parsed)
    todo = [r for r, d in docs.items() if done.get(r) != str(d.get("version") or "")]
    todo.sort(key=lambda r: (bool(docs[r].get("doc_kind")), r))        # 규칙이 비워 둔 문서부터
    redo_kinds = {k.strip() for k in args.redo.split(",") if k.strip()}
    if redo_kinds:                                                      # 옛 지시로 그 갈래가 된 판정 — 맨 앞에서 다시
        pending = set(todo)
        redo = sorted(r for r, j in last.items() if r in docs and r not in pending
                      and j.get("kind") in redo_kinds and j.get("pv") != kind_read.PROMPT_VER)
        todo = redo + todo
        print(f"다시 볼 판정({','.join(sorted(redo_kinds))}) {len(redo)}", flush=True)
    complete, llm = s190.make_complete(args.url, args.model, args.role)
    from zzaimy.generate import model_info
    print(f"판정 모델: {model_info.label(llm)}", flush=True)
    if args.redo_model:
        if not (llm.get("digest") or llm.get("base") or llm.get("adapter")):
            print("지금 모델의 판 정보(digest·바탕 가중치)를 서버에서 읽지 못해 --redo-model 을 건너뜀", flush=True)
        else:
            todo = redo_by_model(last, docs, todo, llm)
    print(f"문서 {len(docs)} · 이미 판정 {len(docs) - len(todo)} · 남음 {len(todo)}", flush=True)
    from zzaimy.app.doc_routing import excluded_kinds
    excluded = set(excluded_kinds())
    t0, n_ok, n_skip = time.time(), 0, 0
    with out.open("a", encoding="utf-8") as fo:
        for i, rel in enumerate(todo, 1):
            if args.max_sec and time.time() - t0 > args.max_sec:
                break
            d = docs[rel]
            got = kind_read.judge(complete, Path(d.get("filename") or rel).stem, d.get("masked_text") or "", str(Path(rel).parent))
            prev = last.get(rel, {}).get("kind")
            if got and not got[0] and prev in excluded:                 # 앞 판정이 빠지는 갈래였는데 이제 모름 — 갈래를 비우라고 적는다
                got = ("", got[1])
            elif not got or not got[0]:
                n_skip += 1
                continue
            fo.write(json.dumps({"rel": rel, "version": str(d.get("version") or ""), "kind": got[0], "why": got[1],
                                 "rule": d.get("doc_kind") or "", "chars": len((d.get("masked_text") or "").strip()), "pv": kind_read.PROMPT_VER,
                                 "llm": llm, "at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False) + "\n")
            fo.flush()
            n_ok += 1
            if i % 200 == 0:
                print(f"  {i}/{len(todo)} · 판정 {n_ok} · 모름·실패 {n_skip} · {time.time() - t0:.0f}초", flush=True)
    print(f"KIND27B 판정 {n_ok} · 모름·실패 {n_skip} · {time.time() - t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
